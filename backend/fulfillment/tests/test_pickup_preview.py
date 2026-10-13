"""Pickup preview checks the supplied bearer without creating delivery facts."""
import json
import uuid
from django.test import TransactionTestCase, override_settings, Client
from customers.models import MemberSession
from fulfillment.models import StoreDeliveryEvent
from fulfillment.store_service import delivery_data
from stores.models import StoreStaff, StoreRequestQuota
from .test_store_delivery import StoreDeliveryFlowTests


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class PickupPreviewTests(TransactionTestCase):
    setUp = StoreDeliveryFlowTests.setUp
    _order = StoreDeliveryFlowTests._order
    _evidence = StoreDeliveryFlowTests._evidence
    _paid_order = StoreDeliveryFlowTests._paid_order
    local_order = StoreDeliveryFlowTests.local_order
    action = StoreDeliveryFlowTests.action

    def preview(self, order, code, **extra):
        token, _ = MemberSession.issue(self.member)
        return Client().post(f'/api/v1/app/store-center/stores/{self.store.id}/orders/{order.id}/pickup-preview',
            json.dumps({'pickupCode': code, **extra}), content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')

    def ready(self):
        order, line = self.local_order('PICKUP')
        self.action(order, 'PREPARE')
        order.refresh_from_db()
        return order, line, delivery_data(order, include_code=True)['pickupCode']

    def test_valid_preview_is_private_and_has_no_side_effects(self):
        order, line, code = self.ready()
        revision, events = order.revision, StoreDeliveryEvent.objects.count()
        result = self.preview(order, code.lower())
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data'], {'verified': True, 'orderId': str(order.id), 'revision': revision, 'deliveryStatus': 'WAITING_PICKUP', 'items': [{'orderLineId': str(line.id), 'deliverableQuantity': 2}]})
        self.assertIn('no-store', result['Cache-Control'])
        self.assertIn('Authorization', result['Vary'])
        self.assertNotIn(code, result.content.decode())
        order.refresh_from_db()
        self.assertEqual(order.revision, revision)
        self.assertEqual(StoreDeliveryEvent.objects.count(), events)
        self.assertEqual(delivery_data(order)['status'], 'WAITING_PICKUP')

    def test_wrong_other_order_and_completed_codes_rejected(self):
        order, _, code = self.ready()
        other, _, other_code = self.ready()
        for value in ('bad', other_code, 'A' * 65):
            result = self.preview(order, value)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()['error']['code'], 'PICKUP_CODE_INVALID')
        self.action(order, 'COMPLETE', pickupCode=code)
        result = self.preview(order, code)
        self.assertEqual(result.json()['error']['code'], 'ALREADY_PICKED_UP')

    def test_unpaid_unprepared_delivery_and_aftersale_block(self):
        unpaid, _ = self.local_order('PICKUP', paid=False)
        self.assertEqual(self.preview(unpaid, 'A' * 26).json()['error']['code'], 'ORDER_NOT_PAID')
        waiting, _ = self.local_order('PICKUP')
        self.assertEqual(self.preview(waiting, 'A' * 26).json()['error']['code'], 'INVALID_TRANSITION')
        delivery, _ = self.local_order('DELIVERY')
        self.assertEqual(self.preview(delivery, 'A' * 26).json()['error']['code'], 'DELIVERY_MODE_MISMATCH')
        order, line, code = self.ready()
        from aftersales.service import apply_case
        apply_case(self.member, line.id, 'REFUND_ONLY', 2, '暂停交付等待售后审核', uuid.uuid4())
        self.assertEqual(self.preview(order, code).json()['error']['code'], 'AFTERSALE_BLOCKS_SHIPMENT')
        self.assertNotIn('pickupCode', delivery_data(order, include_code=True))
        self.assertNotIn('pickupQrDataUrl', delivery_data(order, include_code=True))

    def test_authorization_order_scope_fields_and_rate_are_guarded(self):
        order, _, code = self.ready()
        path = f'/api/v1/app/store-center/stores/{self.store.id}/orders/{order.id}/pickup-preview'
        result = Client().post(path, json.dumps({'pickupCode': code}), content_type='application/json')
        self.assertEqual(result.status_code, 401)
        self.assertIn('no-store', result['Cache-Control'])
        self.assertEqual(self.preview(order, code, bogus=True).status_code, 400)
        StoreStaff.objects.filter(store=self.store, member=self.member).update(enabled=False)
        self.assertEqual(self.preview(order, code).status_code, 403)
        StoreStaff.objects.filter(store=self.store, member=self.member).update(enabled=True)
        from types import SimpleNamespace
        self.assertEqual(self.preview(SimpleNamespace(id=uuid.uuid4()), code).status_code, 404)
        StoreRequestQuota.objects.filter(scope='pickup_preview_view').update(count=20)
        self.assertEqual(self.preview(order, code).status_code, 429)

    def test_partial_refund_preview_uses_remaining_quantity(self):
        order, line, code = self.ready()
        from aftersales.service import apply_case, review_case
        from payments.refunds import prepare_refund, record_verified_refund
        from payments.tests.test_refund_flow import RefundFlowTests
        from accounts.models import AdminAccount, PermissionGroup, GroupPermission
        self.checker = AdminAccount.objects.create_user(login_name='pickup-refund-checker', password='Synthetic checker only!', display_name='合成复核员')
        group = PermissionGroup.objects.create(code='pickup-refund-checker', name='合成退款复核')
        GroupPermission.objects.create(group=group, code='refund.offline.confirm')
        self.checker.permission_groups.add(group)
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '部分数量无需提货', uuid.uuid4())
        review_case(case.id, self.owner, True, '同意部分退款', case.revision)
        intent = prepare_refund(case.id, self.owner, uuid.uuid4())
        self.assertEqual(record_verified_refund(RefundFlowTests.refund_evidence(self, intent))['outcome'], 'SUCCEEDED')
        result = self.preview(order, code)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data']['items'], [{'orderLineId': str(line.id), 'deliverableQuantity': 1}])
        self.assertIn('pickupCode', delivery_data(order, include_code=True))
        self.action(order, 'COMPLETE', pickupCode=code)
        self.assertEqual(delivery_data(order)['status'], 'COMPLETED')

    def test_other_authorized_store_still_cannot_preview_order(self):
        order, _, code = self.ready()
        from inventory.models import Warehouse
        from stores.models import Store
        warehouse = Warehouse.objects.create(code='pickup-other', name='其他仓')
        other = Store.objects.create(warehouse=warehouse, name='其他前置仓', contact_name='测试联系人', contact_phone='13800000000', address='测试地址')
        StoreStaff.objects.create(store=other, member=self.member, permissions=['orders'])
        original = self.store
        self.store = other
        try:
            result = self.preview(order, code)
            self.assertEqual(result.status_code, 404)
            self.assertEqual(result.json()['error']['code'], 'ORDER_NOT_FOUND')
            self.assertNotIn(code, result.content.decode())
        finally:
            self.store = original

    def test_qr_projection_only_customer_paid_ready(self):
        order, _, code = self.ready()
        from fulfillment.qr import voucher_qr_data_url
        self.assertEqual(delivery_data(order, include_code=True)['pickupQrDataUrl'], voucher_qr_data_url(code))
        self.assertNotIn('pickupQrDataUrl', delivery_data(order))
        self.action(order, 'COMPLETE', pickupCode=code)
        self.assertNotIn('pickupQrDataUrl', delivery_data(order, include_code=True))
