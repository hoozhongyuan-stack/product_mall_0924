"""Approved governance: actor scope, shared rights and quantity-specific handoff."""
import uuid
from django.test import TransactionTestCase, override_settings
from fulfillment.tests import test_store_delivery as store_fixture
from fulfillment.service import FulfillmentError
from fulfillment.store_service import operate, delivery_data
from fulfillment.models import StoreDelivery, Carrier, Shipment
from aftersales.service import apply_case, review_case


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class GovernanceFlowTests(TransactionTestCase):
    setUp = store_fixture.StoreDeliveryFlowTests.setUp
    local_order = store_fixture.StoreDeliveryFlowTests.local_order
    action = store_fixture.StoreDeliveryFlowTests.action
    _order = store_fixture.StoreDeliveryFlowTests._order
    _evidence = store_fixture.StoreDeliveryFlowTests._evidence
    _paid_order = store_fixture.StoreDeliveryFlowTests._paid_order

    def test_pickup_success_request_keys_survive_order_detail_get(self):
        from django.test import Client
        from customers.models import MemberSession
        from fulfillment.models import StoreDeliveryEvent
        from fulfillment.store_views import scoped_order_data
        order, _ = self.local_order('PICKUP')
        prepare_key, complete_key = uuid.uuid4(), uuid.uuid4()
        operate(self.member, self.store.id, order.id,
                {'action': 'PREPARE', 'expectedRevision': order.revision}, prepare_key)
        order.refresh_from_db()
        code = delivery_data(order, include_code=True)['pickupCode']
        operate(self.member, self.store.id, order.id,
                {'action': 'COMPLETE', 'expectedRevision': order.revision, 'pickupCode': code}, complete_key)
        token, _ = MemberSession.issue(self.member)
        client = Client()
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        expected = {'PREPARE': str(prepare_key), 'COMPLETE': str(complete_key)}
        paths = [f'/api/v1/app/store-center/stores/{self.store.id}/orders/{order.id}',
                 f'/api/v1/app/orders/{order.id}']
        for path in paths:
            result = client.get(path, **headers)
            self.assertEqual(result.status_code, 200, result.content)
            events = result.json()['data']['storeDelivery']['events']
            self.assertEqual({row['action']: row['requestKey'] for row in events}, expected)
            self.assertTrue(all(set(row) == {'action', 'requestKey', 'createdAt'} for row in events))
        client.get('/api/v1/admin/auth/csrf')
        import json
        login = client.post('/api/v1/admin/auth/login', json.dumps(
            {'loginName': self.owner.login_name, 'password': 'Long test password 2026!'}), content_type='application/json')
        self.assertEqual(login.status_code, 200)
        result = client.get(f'/api/v1/admin/orders/{order.id}')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual({row['requestKey'] for row in result.json()['data']['storeDelivery']['events']}, set(expected.values()))
        self.assertEqual(StoreDeliveryEvent.objects.filter(order=order).count(), 2)
        self.assertEqual(scoped_order_data(order, uuid.uuid4())['storeDelivery']['events'], [])

    def test_delivery_failure_request_key_survives_order_detail_get(self):
        from django.test import Client
        from customers.models import MemberSession
        order, _ = self.local_order('DELIVERY')
        self.action(order, 'PREPARE')
        self.action(order, 'DISPATCH')
        order.refresh_from_db()
        failed_key = uuid.uuid4()
        operate(self.member, self.store.id, order.id,
                {'action': 'FAIL_DELIVERY', 'expectedRevision': order.revision, 'note': '顾客不在现场，配送失败'}, failed_key)
        token, _ = MemberSession.issue(self.member)
        result = Client().get(f'/api/v1/app/store-center/stores/{self.store.id}/orders/{order.id}',
                              HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(result.status_code, 200, result.content)
        events = result.json()['data']['storeDelivery']['events']
        self.assertEqual(events[0]['action'], 'FAIL_DELIVERY')
        self.assertEqual(events[0]['requestKey'], str(failed_key))
        self.assertEqual(len(events), 3)

    def test_partial_hold_handoffs_only_available_units_then_release_reopens(self):
        from fulfillment.read import fulfillment_data, completion_fact
        order, line = self.local_order('PICKUP')
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '一件商品申请退款', uuid.uuid4())
        self.action(order, 'PREPARE')
        code = delivery_data(order, include_code=True)['pickupCode']
        self.action(order, 'COMPLETE', pickupCode=code)
        order.refresh_from_db()
        progress = fulfillment_data(order)['items'][str(line.id)]
        self.assertEqual(progress['deliveredQuantity'], 1)
        self.assertEqual(progress['availableQuantity'], 0)
        self.assertFalse(completion_fact(order))
        review_case(case.id, self.owner, False, '顾客决定保留商品', case.revision)
        self.action(order, 'PREPARE')
        self.action(order, 'COMPLETE', pickupCode=delivery_data(order, include_code=True)['pickupCode'])
        self.assertEqual(fulfillment_data(order)['items'][str(line.id)]['deliveredQuantity'], 2)
        self.assertTrue(completion_fact(order))

    def test_delivery_exception_cannot_complete_until_retry(self):
        order, _ = self.local_order('DELIVERY')
        self.action(order, 'PREPARE')
        self.action(order, 'DISPATCH')
        self.action(order, 'FAIL_DELIVERY', note='顾客不在现场，配送失败')
        self.assertEqual(StoreDelivery.objects.get(order=order).status, 'EXCEPTION')
        with self.assertRaises(FulfillmentError):
            self.action(order, 'COMPLETE', note='尚未再次配送不能送达')
        self.action(order, 'RETRY_DELIVERY', note='已联系顾客继续配送')
        self.action(order, 'COMPLETE', note='已当面交付顾客')
        self.assertEqual(StoreDelivery.objects.get(order=order).status, 'COMPLETED')

    def test_platform_cannot_operate_local_handoff(self):
        order, _ = self.local_order('PICKUP')
        with self.assertRaises(FulfillmentError) as caught:
            operate(None, self.store.id, order.id, {'action': 'PREPARE', 'expectedRevision': order.revision}, uuid.uuid4(), admin_actor=self.owner)
        self.assertEqual(caught.exception.code, 'FULFILLMENT_OWNER_MISMATCH')

    def test_staff_cannot_ship_platform_express(self):
        order, _ = self.local_order('EXPRESS')
        Carrier.objects.create(code='SF', name='顺丰', enabled=True)
        with self.assertRaises(FulfillmentError):
            self.action(order, 'SHIP', carrierCode='SF', trackingNo='SF123456')
        self.assertFalse(Shipment.objects.filter(order=order).exists())

    def test_platform_and_store_consume_same_snapshot_rights(self):
        from fulfillment.models import RedeemVoucher, RedeemEvent
        from fulfillment.service import redeem
        from fulfillment.redemption import store_redeem
        from fulfillment.codes import voucher_code
        product = self.sku.product
        product.redeem_scope = 'BOTH'
        product.redeem_store_ids = [str(self.store.id)]
        product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        voucher.refresh_from_db()
        body = {'orderLineId': str(line.id), 'voucherCode': voucher_code(line.id, voucher.nonce), 'quantity': 1, 'expectedRevision': voucher.revision, 'note': '前置仓完成服务'}
        key = uuid.uuid4()
        store_redeem(self.member, self.store.id, order.id, body, key)
        store_redeem(self.member, self.store.id, order.id, body, key)
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 2)
        self.assertEqual(RedeemEvent.objects.filter(voucher=voucher).count(), 2)
        event = RedeemEvent.objects.get(voucher=voucher, store_id=self.store.id)
        self.assertEqual(event.store_staff_actor_id, self.member.id)
        self.assertIsNone(event.actor_id)
        with self.assertRaises(FulfillmentError):
            store_redeem(self.member, self.store.id, order.id, {**body, 'quantity': 2}, key)

    def test_store_only_scope_is_visible_and_platform_denied(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem, lookup_voucher
        from fulfillment.codes import voucher_code
        from fulfillment.store_views import scoped_orders
        product = self.sku.product
        product.redeem_scope = 'STORE'
        product.redeem_store_ids = [str(self.store.id)]
        product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        self.assertTrue(scoped_orders(self.store.id).filter(pk=order.pk).exists())
        self.assertIsNone(order.store_id)
        with self.assertRaises(FulfillmentError) as denied:
            redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        self.assertEqual(denied.exception.code, 'REDEMPTION_SCOPE_DENIED')
        with self.assertRaises(FulfillmentError):
            lookup_voucher(voucher_code(line.id, voucher.nonce), self.owner)

    def test_express_partial_freeze_has_two_quantity_batches(self):
        from fulfillment.service import ship_order, confirm_receipt
        from fulfillment.read import fulfillment_data, completion_fact
        from fulfillment.models import ShipmentLine
        order, line = self._paid_order('SHIP')
        Carrier.objects.create(code='SF', name='顺丰', enabled=True)
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '暂缓交付一件商品', uuid.uuid4())
        order.refresh_from_db()
        key = uuid.uuid4()
        ship_order(order.id, self.owner, 'SF', 'SF123456', order.revision, key)
        ship_order(order.id, self.owner, 'SF', 'SF123456', order.revision, key)
        self.assertEqual(ShipmentLine.objects.get(order_line=line).quantity, 1)
        confirm_receipt(self.member, order.id)
        self.assertFalse(completion_fact(order))
        review_case(case.id, self.owner, False, '顾客仍需剩余商品', case.revision)
        order.refresh_from_db()
        ship_order(order.id, self.owner, 'SF', 'SF654321', order.revision, uuid.uuid4())
        data = fulfillment_data(order)
        self.assertEqual(len(data['shipments']), 2)
        self.assertEqual(data['items'][str(line.id)]['dispatchedQuantity'], 2)
        confirm_receipt(self.member, order.id)
        self.assertTrue(completion_fact(order))

    def test_partial_pickup_and_unshipped_refund_use_original_stock(self):
        from accounts.models import AdminAccount, PermissionGroup, GroupPermission
        from payments.tests import test_refund_flow as refunds
        from payments.refunds import prepare_refund, record_verified_refund
        from inventory.models import InventoryLedger
        from fulfillment.read import completion_fact
        self.checker = AdminAccount.objects.create_user(login_name='governance-checker', display_name='复核员', password='Synthetic checker password 2026!', kind='STAFF', enabled=True)
        group = PermissionGroup.objects.create(code='governance_checker', name='退款复核')
        GroupPermission.objects.create(group=group, code='refund.offline.confirm')
        self.checker.permission_groups.add(group)
        order, line = self.local_order('PICKUP')
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '一件商品申请退款', uuid.uuid4())
        self.action(order, 'PREPARE')
        self.action(order, 'COMPLETE', pickupCode=delivery_data(order, include_code=True)['pickupCode'])
        review_case(case.id, self.owner, True, '未交付部分批准退款', case.revision)
        intent = prepare_refund(case.id, self.owner, uuid.uuid4())
        evidence = refunds.RefundFlowTests.refund_evidence(self, intent)
        self.assertEqual(record_verified_refund(evidence)['outcome'], 'SUCCEEDED')
        self.assertEqual(record_verified_refund(evidence)['outcome'], 'SUCCEEDED')
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.on_hand_base_units, 9)
        self.assertEqual(InventoryLedger.objects.filter(refund_order_line=line, movement_type='REFUND').count(), 1)
        self.assertTrue(completion_fact(order))

    def test_wrong_code_expiry_and_disabled_store_create_no_redemption(self):
        from fulfillment.models import RedeemVoucher, RedeemEvent
        from fulfillment.redemption import store_redeem
        from fulfillment.codes import voucher_code
        product = self.sku.product
        product.redeem_scope, product.redeem_store_ids = 'BOTH', [str(self.store.id)]
        product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        body = {'orderLineId': str(line.id), 'voucherCode': 'WRONG', 'quantity': 1, 'expectedRevision': voucher.revision, 'note': ''}
        with self.assertRaises(FulfillmentError) as error:
            store_redeem(self.member, self.store.id, order.id, body, uuid.uuid4())
        self.assertEqual(error.exception.code, 'VOUCHER_CODE_INVALID')
        self.store.enabled = False
        self.store.save(update_fields=['enabled'])
        body['voucherCode'] = voucher_code(line.id, voucher.nonce)
        with self.assertRaises(FulfillmentError) as error:
            store_redeem(self.member, self.store.id, order.id, body, uuid.uuid4())
        self.assertEqual(error.exception.code, 'STORE_DISABLED')
        self.assertFalse(RedeemEvent.objects.filter(voucher=voucher).exists())

    def test_changed_product_scope_does_not_change_existing_rights(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem
        from fulfillment.redemption import permitted
        product = self.sku.product
        product.redeem_scope, product.redeem_store_ids = 'BOTH', [str(self.store.id)]
        product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        order, line = self._paid_order()
        product.redeem_scope, product.redeem_store_ids = 'PLATFORM', []
        product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        line.refresh_from_db()
        self.assertTrue(permitted(line, self.store.id))
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 1)

    def test_store_redemption_api_revalidates_scope_revision_and_credentials(self):
        import json
        from django.test import Client
        from customers.models import MemberSession
        from fulfillment.models import RedeemVoucher, RedeemEvent
        from fulfillment.codes import voucher_code
        from fulfillment.store_views import scoped_orders
        product = self.sku.product
        product.redeem_scope, product.redeem_store_ids = 'BOTH', [str(self.store.id)]
        product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        token, _ = MemberSession.issue(self.member)
        client = Client()
        url = f'/api/v1/app/store-center/stores/{self.store.id}/orders/{order.id}/redemptions'
        body = {'orderLineId': str(line.id), 'voucherCode': voucher_code(line.id, voucher.nonce), 'quantity': 1, 'expectedRevision': voucher.revision, 'note': '已提供实际服务'}
        headers = {'HTTP_AUTHORIZATION': 'Bearer '+token, 'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
        self.assertEqual(client.post(url, json.dumps(body), content_type='application/json').status_code, 401)
        for patch, expected in [({'quantity': True}, 400), ({'orderLineId': 'invalid'}, 400), ({'expectedRevision': 0}, 409), ({'voucherCode': 'wrong'}, 409), ({'quantity': 3}, 409)]:
            result = client.post(url, json.dumps({**body, **patch}), content_type='application/json', **headers)
            self.assertEqual(result.status_code, expected, result.content)
        result = client.post(url, json.dumps(body), content_type='application/json', **headers)
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()['data']
        self.assertEqual(data['items'][0]['fulfillment']['redeemedQuantity'], 1)
        self.assertNotIn('voucherCode', data['items'][0]['fulfillment'])
        self.assertEqual(RedeemEvent.objects.filter(voucher=voucher).count(), 1)

    def test_redemption_request_identity_cannot_cross_vouchers_or_actors(self):
        from fulfillment.models import RedeemVoucher, RedeemEvent
        from fulfillment.service import redeem
        from fulfillment.redemption import store_redeem
        from fulfillment.codes import voucher_code
        product = self.sku.product
        product.redeem_scope, product.redeem_store_ids = 'BOTH', [str(self.store.id)]
        product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        first, first_line = self._paid_order()
        second, second_line = self._paid_order()
        v1, v2 = RedeemVoucher.objects.get(order_line=first_line), RedeemVoucher.objects.get(order_line=second_line)
        key = uuid.uuid4()
        redeem(v1.id, self.owner, 1, v1.revision, key)
        for target, qty, revision in [(v2, 1, v2.revision), (v1, 1, v1.revision+1)]:
            with self.assertRaises(FulfillmentError) as conflict:
                redeem(target.id, self.owner, qty, revision, key)
            self.assertEqual(conflict.exception.code, 'IDEMPOTENCY_CONFLICT')
        body = {'orderLineId': str(second_line.id), 'voucherCode': voucher_code(second_line.id, v2.nonce), 'quantity': 1, 'expectedRevision': v2.revision, 'note': ''}
        with self.assertRaises(FulfillmentError) as conflict:
            store_redeem(self.member, self.store.id, second.id, body, key)
        self.assertEqual(conflict.exception.code, 'IDEMPOTENCY_CONFLICT')
        store_key = uuid.uuid4()
        store_redeem(self.member, self.store.id, second.id, body, store_key)
        with self.assertRaises(FulfillmentError) as conflict:
            store_redeem(self.member, self.store.id, first.id, {**body, 'orderLineId': str(first_line.id), 'voucherCode': voucher_code(first_line.id, v1.nonce)}, store_key)
        self.assertEqual(conflict.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(RedeemEvent.objects.filter(request_key=key).count(), 1)
        self.assertEqual(RedeemEvent.objects.filter(request_key=store_key).count(), 1)

    def test_return_refund_cannot_close_unhanded_units(self):
        from unittest.mock import patch
        from fulfillment.read import fulfillment_data, page_fulfillment_summaries, completion_fact
        order, line = self.local_order('PICKUP')
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '暂不交付一件商品', uuid.uuid4())
        self.action(order, 'PREPARE')
        self.action(order, 'COMPLETE', pickupCode=delivery_data(order, include_code=True)['pickupCode'])
        review_case(case.id, self.owner, False, '顾客仍需未交付商品', case.revision)
        # One of the previously handed units has a completed RETURN_REFUND;
        # that quantity is already represented in deliveredQuantity.
        facts = {line.id: {'refundedQuantity': 1, 'refundedUnshippedQuantity': 0, 'reservedQuantity': 0, 'reservedUnshippedQuantity': 0}}
        with patch('aftersales.read.line_summaries', return_value=facts):
            detail = fulfillment_data(order)
            self.assertEqual(detail['items'][str(line.id)]['remainingPhysicalQuantity'], 1)
            self.assertNotEqual(detail['fulfillmentStatus'], 'COMPLETED')
            self.assertNotEqual(page_fulfillment_summaries([order])[order.id]['fulfillmentStatus'], 'COMPLETED')
            self.assertFalse(completion_fact(order))

    def test_new_redemption_requires_digest_and_legacy_duplicate_keys_are_preserved(self):
        from django.db import connection, transaction, DatabaseError
        from fulfillment.models import RedeemVoucher, RedeemEvent
        from fulfillment.service import redeem
        first, first_line = self._paid_order()
        second, second_line = self._paid_order()
        v1, v2 = RedeemVoucher.objects.get(order_line=first_line), RedeemVoucher.objects.get(order_line=second_line)
        with self.assertRaises(DatabaseError), transaction.atomic():
            RedeemEvent.objects.create(voucher=v1, actor=self.owner, kind='USE', quantity=1, remaining_quantity=1, request_key=uuid.uuid4(), request_digest='')
        with self.assertRaises(DatabaseError), transaction.atomic():
            RedeemEvent.objects.create(voucher=v1, actor=self.owner, kind='USE', quantity=1, remaining_quantity=1, request_key=uuid.uuid4(), request_digest='z'*64)
        key = uuid.uuid4()
        constraint = next(row for row in RedeemEvent._meta.constraints if row.name == 'redeem_use_request_global_unique')
        with connection.schema_editor(collect_sql=True, atomic=False) as editor:
            index_sql = str(constraint.create_sql(RedeemEvent, editor))
        # Isolated transaction emulates immutable rows from before the insert
        # guard existed, then builds the real migration index over both rows.
        # Rollback restores the guard, index and fixture after the assertion.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('ALTER TABLE fulfillment_redeem_event DISABLE TRIGGER fg_redemption_request_identity')
            for voucher in (v1, v2):
                RedeemEvent.objects.create(voucher=voucher, actor=self.owner, kind='USE', quantity=1, remaining_quantity=1, request_key=key, request_digest='')
            with connection.cursor() as cursor:
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cursor.execute('ALTER TABLE fulfillment_redeem_event ENABLE TRIGGER fg_redemption_request_identity')
                cursor.execute('DROP INDEX redeem_use_request_global_unique')
                cursor.execute(index_sql)
            self.assertEqual(RedeemEvent.objects.filter(request_key=key, request_digest='').count(), 2)
            with self.assertRaises(FulfillmentError) as occupied:
                redeem(v1.id, self.owner, 1, v1.revision, key)
            self.assertEqual(occupied.exception.code, 'IDEMPOTENCY_CONFLICT')
            transaction.set_rollback(True)
