"""Admin list operations retain the store handoff and credential rules."""
import json
import time
import uuid
from unittest.mock import patch
from django.test import Client, TransactionTestCase, override_settings
from accounts.models import AdminAccount
from fulfillment.models import StoreDeliveryEvent
from fulfillment.store_service import delivery_data
from . import test_store_delivery as fixtures


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class AdminLocalActionsTests(TransactionTestCase):
    setUp = fixtures.StoreDeliveryFlowTests.setUp
    _order = fixtures.StoreDeliveryFlowTests._order
    _evidence = fixtures.StoreDeliveryFlowTests._evidence
    _paid_order = fixtures.StoreDeliveryFlowTests._paid_order
    local_order = fixtures.StoreDeliveryFlowTests.local_order
    action = fixtures.StoreDeliveryFlowTests.action

    def make_client(self, actor=None, csrf=False):
        client = Client(enforce_csrf_checks=csrf)
        actor = actor or self.owner
        client.force_login(actor)
        session = client.session
        session['admin_last_active'] = time.time()
        session['admin_auth_version'] = actor.auth_version
        session.save()
        return client

    def post(self, order, action, key=None, **fields):
        order.refresh_from_db()
        return self.make_client().post(f'/api/v1/admin/orders/{order.id}/local-fulfillment',
            json.dumps({'action': action, 'expectedRevision': order.revision, **fields}),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(key or uuid.uuid4()))

    def test_delivery_sequence_immutable_warehouse_actor_and_stock(self):
        order, line = self.local_order('DELIVERY')
        warehouse = line.warehouse_id
        self.assertEqual(self.post(order, 'DISPATCH').status_code, 409)
        prepared = self.post(order, 'PREPARE')
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared.json()['data']['localDeliveryEventCount'], 1)
        self.assertEqual(self.post(order, 'DISPATCH', note='配送员：张三').status_code, 200)
        self.assertEqual(self.post(order, 'COMPLETE', note='bad').status_code, 409)
        self.assertEqual(self.post(order, 'COMPLETE', note='已交付指定餐桌并当面确认').status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.warehouse_id, warehouse)
        events = list(StoreDeliveryEvent.objects.filter(order=order))
        self.assertEqual(len(events), 3)
        self.assertTrue(all(event.actor_id is None and event.admin_actor_id == self.owner.id for event in events))
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.on_hand_base_units, 8)

    def test_pickup_preview_never_exposes_bearer_and_handoff_checks_again(self):
        order, line = self.local_order('PICKUP')
        self.assertEqual(self.post(order, 'PREPARE').status_code, 200)
        code = delivery_data(order, include_code=True)['pickupCode']
        path = f'/api/v1/admin/orders/{order.id}/pickup-preview'
        result = self.make_client().post(path, json.dumps({'pickupCode': code}), content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data']['items'], [{'orderLineId': str(line.id), 'deliverableQuantity': 2}])
        self.assertNotIn(code, result.content.decode())
        self.assertEqual(self.post(order, 'COMPLETE', pickupCode='wrong').status_code, 409)
        self.assertEqual(self.post(order, 'COMPLETE', pickupCode=code).status_code, 200)
        self.assertEqual(self.post(order, 'COMPLETE', pickupCode=code).status_code, 409)

    def test_idempotent_replay_and_changed_payload_conflict(self):
        order, _ = self.local_order('DELIVERY')
        key = uuid.uuid4()
        body = {'action': 'PREPARE', 'expectedRevision': order.revision}
        path = f'/api/v1/admin/orders/{order.id}/local-fulfillment'
        for _ in range(2):
            result = self.make_client().post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(key))
            self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(StoreDeliveryEvent.objects.filter(request_key=key).count(), 1)
        result = self.make_client().post(path, json.dumps({**body, 'note': 'changed'}), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(key))
        self.assertEqual(result.json()['error']['code'], 'IDEMPOTENCY_CONFLICT')

    def test_permission_csrf_revision_unpaid_and_partial_pickup_rejected(self):
        order, _ = self.local_order('PICKUP')
        sub = AdminAccount.objects.create_user('no-local-permission', 'Test password 2026!', kind='SUB')
        path = f'/api/v1/admin/orders/{order.id}/local-fulfillment'
        body = {'action': 'PREPARE', 'expectedRevision': order.revision}
        for client, expected in [(Client(), 401), (self.make_client(sub), 403), (self.make_client(csrf=True), 403)]:
            result = client.post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
            self.assertEqual(result.status_code, expected)
        self.assertEqual(self.post(order, 'PREPARE', quantity=1).status_code, 400)
        self.assertEqual(self.make_client().post(path, json.dumps({**body, 'expectedRevision': 0}), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code, 409)
        unpaid, _ = self.local_order('DELIVERY', paid=False)
        self.assertEqual(self.post(unpaid, 'PREPARE').json()['error']['code'], 'ORDER_NOT_PAID')

    def test_active_aftersale_blocks_final_handoff_after_successful_preview(self):
        from aftersales.service import apply_case
        order, line = self.local_order('PICKUP')
        self.assertEqual(self.post(order, 'PREPARE').status_code, 200)
        code = delivery_data(order, include_code=True)['pickupCode']
        apply_case(self.member, line.id, 'REFUND_ONLY', 1, '暂停交付等待售后审核', uuid.uuid4())
        result = self.post(order, 'COMPLETE', pickupCode=code)
        self.assertEqual(result.json()['error']['code'], 'AFTERSALE_BLOCKS_SHIPMENT')
        self.assertEqual(delivery_data(order)['status'], 'WAITING_PICKUP')

    def test_replay_isolated_from_member_actor_and_audit_is_atomic(self):
        from accounts.models import AuditLog
        from fulfillment.store_service import operate
        from fulfillment.service import FulfillmentError
        order, _ = self.local_order('DELIVERY')
        key = uuid.uuid4()
        body = {'action': 'PREPARE', 'expectedRevision': order.revision}
        path = f'/api/v1/admin/orders/{order.id}/local-fulfillment'
        client = self.make_client()
        client.post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(key))
        client.post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(key))
        self.assertEqual(AuditLog.objects.filter(action_code='fulfillment.local.prepare').count(), 1)
        with self.assertRaises(FulfillmentError) as mismatch:
            operate(self.member, self.store.id, order.id, body, key)
        self.assertEqual(mismatch.exception.code, 'IDEMPOTENCY_CONFLICT')
        other, _ = self.local_order('DELIVERY')
        with patch('fulfillment.admin_local_views.audit', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.post(other, 'PREPARE')
        self.assertFalse(StoreDeliveryEvent.objects.filter(order=other).exists())
        self.assertEqual(delivery_data(other)['status'], 'WAITING_PREPARATION')

    def test_live_disabled_actor_cannot_operate_after_lock(self):
        from fulfillment.store_service import operate
        from fulfillment.service import FulfillmentError
        order, _ = self.local_order('DELIVERY')
        AdminAccount.objects.filter(pk=self.owner.id).update(enabled=False)
        with self.assertRaises(FulfillmentError) as denied:
            operate(None, self.store.id, order.id, {'action': 'PREPARE', 'expectedRevision': order.revision}, uuid.uuid4(), admin_actor=self.owner)
        self.assertEqual(denied.exception.code, 'PERMISSION_DENIED')

    def test_admin_tracking_preserves_read_permission_and_private_cache(self):
        from fulfillment.models import Carrier
        from fulfillment.service import ship_order
        order, _ = self.local_order('EXPRESS')
        path = f'/api/v1/admin/orders/{order.id}/tracking'
        self.assertEqual(self.make_client().get(path).json()['error']['code'], 'NOT_SHIPPED')
        Carrier.objects.create(code='SF', name='顺丰速运', enabled=True)
        ship_order(order.id, self.owner, 'SF', 'SF123456', order.revision, uuid.uuid4())
        result = self.make_client().get(path)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['trackingNo'], 'SF123456')
        self.assertIn('no-store', result['Cache-Control'])
        sub = AdminAccount.objects.create_user('no-tracking-permission', 'Test password 2026!', kind='SUB')
        self.assertEqual(self.make_client(sub).get(path).status_code, 403)

    def test_admin_wechat_query_returns_no_customer_bearer_and_validates_method(self):
        from payments.models import WechatPaymentAttempt
        from django.utils import timezone
        from datetime import timedelta
        order, _ = self.local_order('PICKUP', paid=False)
        path = f'/api/v1/admin/orders/{order.id}/wechat/query'
        self.assertEqual(self.make_client().post(path, '{}', content_type='application/json').json()['error']['code'], 'WECHAT_ORDER_INVALID')
        product = self.sku.product
        product.fulfillment_kind = 'REDEEM'
        product.save(update_fields=['fulfillment_kind'])
        order = self._order('WECHAT')
        path = f'/api/v1/admin/orders/{order.id}/wechat/query'
        result = self.make_client().post(path, '{}', content_type='application/json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['paymentState'], 'NOTPAY')
        attempt = WechatPaymentAttempt.objects.create(order=order, out_trade_no='adminquery-test01', app_id='wx-payment-test', merchant_id='merchant-test', payer_openid='payer-test', amount_fen=order.payable_fen, expires_at=timezone.now()+timedelta(minutes=10), trade_state='NOTPAY')
        with patch('payments.admin_query._query') as provider, patch('payments.admin_query.get_gateway'):
            result = self.make_client().post(path, '{}', content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        provider.assert_called_once()
        self.assertEqual(result.json()['data']['paymentState'], 'NOTPAY')
        self.assertNotIn('pickupCode', result.content.decode())
        self.assertNotIn('voucherCode', result.content.decode())
        self.assertEqual(self.make_client().get(path).status_code, 405)
        self.assertEqual(self.make_client().post(path, '{"amountFen": 1}', content_type='application/json').status_code, 400)

    def test_missing_key_invalid_fields_mode_and_other_order_preview(self):
        order, _ = self.local_order('EXPRESS')
        self.assertEqual(self.post(order, 'PREPARE').json()['error']['code'], 'DELIVERY_MODE_MISMATCH')
        self.assertEqual(self.post(order, 'SHIP', carrierCode='SF', trackingNo='SF123456').status_code, 400)
        local, _ = self.local_order('PICKUP')
        path = f'/api/v1/admin/orders/{local.id}/local-fulfillment'
        self.assertEqual(self.make_client().post(path, '{"action":"PREPARE","expectedRevision":1}', content_type='application/json').status_code, 400)
        self.assertEqual(self.post(local, 'INVALID').status_code, 400)
        self.assertEqual(self.post(local, 'PREPARE', note='x'*501).status_code, 400)
        self.assertEqual(self.post(local, 'PREPARE').status_code, 200)
        other, _ = self.local_order('PICKUP')
        self.assertEqual(self.post(other, 'PREPARE').status_code, 200)
        wrong_code = delivery_data(other, include_code=True)['pickupCode']
        result = self.make_client().post(f'/api/v1/admin/orders/{local.id}/pickup-preview', json.dumps({'pickupCode': wrong_code}), content_type='application/json')
        self.assertEqual(result.json()['error']['code'], 'PICKUP_CODE_INVALID')
        result = self.make_client().post(f'/api/v1/admin/orders/{uuid.uuid4()}/local-fulfillment', '{"action":"PREPARE","expectedRevision":1}', content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.status_code, 404)

    def test_wechat_query_permission_and_provider_error_safely_returned(self):
        from orders.admin_payment_query import query
        from payments.service import PaymentError
        from payments.wechat_gateway import WechatGatewayError
        from payments.models import WechatPaymentAttempt
        from django.utils import timezone
        from datetime import timedelta
        order, _ = self.local_order('DELIVERY', paid=False)
        product = self.sku.product
        product.fulfillment_kind = 'REDEEM'
        product.save(update_fields=['fulfillment_kind'])
        order = self._order('WECHAT')
        path = f'/api/v1/admin/orders/{order.id}/wechat/query'
        WechatPaymentAttempt.objects.create(order=order, out_trade_no='adminquery-test02', app_id='wx-payment-test', merchant_id='merchant-test', payer_openid='payer-test', amount_fen=order.payable_fen, expires_at=timezone.now()+timedelta(minutes=10))
        path = f'/api/v1/admin/orders/{order.id}/wechat/query'
        sub = AdminAccount.objects.create_user('no-payment-query', 'Test password 2026!', kind='SUB')
        self.assertEqual(self.make_client(sub).post(path, '{}', content_type='application/json').status_code, 403)
        with patch('payments.admin_query._query', side_effect=WechatGatewayError('微信服务暂不可用', 'WECHAT_UNAVAILABLE', 503)), patch('payments.admin_query.get_gateway'):
            result = self.make_client().post(path, '{}', content_type='application/json')
        self.assertEqual(result.status_code, 503, result.content)
        AdminAccount.objects.filter(pk=self.owner.id).update(enabled=False)
        with self.assertRaises(PaymentError) as denied:
            query(order.id, self.owner)
        self.assertEqual(denied.exception.code, 'PERMISSION_DENIED')

    def test_admin_delivery_history_is_bounded_and_not_in_customer_response(self):
        from orders.queries import admin_order_data
        from orders.service import order_data
        order, _ = self.local_order('DELIVERY')
        self.action(order, 'PREPARE', note='前置仓已经备货')
        self.assertEqual(self.post(order, 'DISPATCH', note='配送员：张三，联系电话 13800000000').status_code, 200)
        self.assertEqual(self.post(order, 'COMPLETE', note='已交付桌号 A01，顾客确认').status_code, 200)
        admin = admin_order_data(order)
        self.assertEqual(admin['localDeliveryEventCount'], 3)
        self.assertEqual([event['action'] for event in admin['localDeliveryEvents']], ['COMPLETE', 'DISPATCH', 'PREPARE'])
        self.assertEqual(admin['localDeliveryEvents'][0]['actorKind'], 'ADMIN')
        self.assertEqual(admin['localDeliveryEvents'][0]['requestKey'], str(StoreDeliveryEvent.objects.filter(order=order, action='COMPLETE').get().request_key))
        self.assertEqual(admin['localDeliveryEvents'][2]['actorKind'], 'STORE_STAFF')
        self.assertNotIn('pickupCode', json.dumps(admin['localDeliveryEvents']))
        self.assertNotIn('localDeliveryEvents', order_data(order))
        for _ in range(22):
            StoreDeliveryEvent.objects.create(order=order, admin_actor=self.owner, action='PREPARE', request_key=uuid.uuid4(), request_digest='a'*64, note='只读历史窗口测试')
        # Relations are joined: history uses two queries regardless of record count.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as captured:
            admin = admin_order_data(order)
        history_reads = [query for query in captured.captured_queries if 'fulfillment_store_delivery_event' in query['sql']]
        self.assertEqual(len(history_reads), 2)
        self.assertEqual(admin['localDeliveryEventCount'], 25)
        self.assertEqual(len(admin['localDeliveryEvents']), 20)

    def test_order_scoped_redemption_rejects_another_orders_voucher_and_replays(self):
        from fulfillment.models import RedeemVoucher, RedeemEvent
        from accounts.models import AuditLog
        order, line = self._paid_order('REDEEM')
        other, other_line = self._paid_order('REDEEM')
        voucher = RedeemVoucher.objects.get(order_line=line)
        other_voucher = RedeemVoucher.objects.get(order_line=other_line)
        client = self.make_client()
        path = f'/api/v1/admin/orders/{order.id}/redemptions/{other_voucher.id}/use'
        body = {'quantity': 1, 'expectedRevision': other_voucher.revision}
        result = client.post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(result.json()['error']['code'], 'VOUCHER_ORDER_MISMATCH')
        self.assertFalse(RedeemEvent.objects.filter(voucher=other_voucher).exists())
        path = f'/api/v1/admin/orders/{order.id}/redemptions/{voucher.id}/use'
        body = {'quantity': 1, 'expectedRevision': voucher.revision}
        key = uuid.uuid4()
        for _ in range(2):
            result = client.post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(key))
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(result.json()['data']['order']['orderId'], str(order.id))
        self.assertEqual(RedeemEvent.objects.filter(voucher=voucher).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action_code='fulfillment.redeem.use', object_id=str(voucher.id)).count(), 1)
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 1)

    def test_order_scoped_redemption_permission_and_audit_failure_are_atomic(self):
        from fulfillment.models import RedeemVoucher, RedeemEvent
        order, line = self._paid_order('REDEEM')
        voucher = RedeemVoucher.objects.get(order_line=line)
        path = f'/api/v1/admin/orders/{order.id}/redemptions/{voucher.id}/use'
        body = {'quantity': 1, 'expectedRevision': voucher.revision}
        sub = AdminAccount.objects.create_user('no-scoped-redeem', 'Test password 2026!', kind='SUB')
        self.assertEqual(self.make_client(sub).post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code, 403)
        self.assertEqual(self.make_client(csrf=True).post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code, 403)
        with patch('fulfillment.admin_local_views.audit', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.make_client().post(path, json.dumps(body), content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertFalse(RedeemEvent.objects.filter(voucher=voucher).exists())
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 0)
