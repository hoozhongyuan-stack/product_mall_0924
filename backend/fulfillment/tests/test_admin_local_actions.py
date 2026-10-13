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

    def test_platform_cannot_operate_warehouse_handoffs(self):
        for mode in ('PICKUP', 'DELIVERY'):
            order, _ = self.local_order(mode)
            for action in ('PREPARE', 'DISPATCH', 'COMPLETE'):
                result = self.post(order, action)
                self.assertEqual(result.status_code, 403, result.content)
                self.assertEqual(result.json()['error']['code'], 'FULFILLMENT_OWNER_MISMATCH')
            self.assertFalse(StoreDeliveryEvent.objects.filter(order=order).exists())

    def test_platform_cannot_verify_pickup_customer_bearer(self):
        order, _ = self.local_order('PICKUP')
        self.action(order, 'PREPARE')
        code = delivery_data(order, include_code=True)['pickupCode']
        result = self.make_client().post(f'/api/v1/admin/orders/{order.id}/pickup-preview', json.dumps({'pickupCode': code}), content_type='application/json')
        self.assertEqual(result.status_code, 403)
        self.assertNotIn(code, result.content.decode())

    def test_local_entry_retains_authentication_permissions_and_csrf(self):
        order, _ = self.local_order('PICKUP')
        sub = AdminAccount.objects.create_user('no-local-permission', 'Test password 2026!', kind='SUB')
        path = f'/api/v1/admin/orders/{order.id}/local-fulfillment'
        body = json.dumps({'action': 'PREPARE', 'expectedRevision': order.revision})
        for client, expected in [(Client(), 401), (self.make_client(sub), 403), (self.make_client(csrf=True), 403)]:
            result = client.post(path, body, content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
            self.assertEqual(result.status_code, expected)

    def test_platform_retains_bounded_readonly_warehouse_history(self):
        from orders.queries import admin_order_data
        from orders.service import order_data
        order, _ = self.local_order('DELIVERY')
        self.action(order, 'PREPARE', note='前置仓已备货')
        self.action(order, 'DISPATCH', note='配送人员出发')
        self.action(order, 'COMPLETE', note='已交付顾客并确认')
        for _ in range(22):
            StoreDeliveryEvent.objects.create(order=order, actor=self.member, action='PREPARE', request_key=uuid.uuid4(), request_digest='a'*64, note='只读历史窗口测试')
        admin = admin_order_data(order)
        self.assertEqual(admin['localDeliveryEventCount'], 25)
        self.assertEqual(len(admin['localDeliveryEvents']), 20)
        self.assertTrue(all(event['actorKind'] == 'STORE_STAFF' for event in admin['localDeliveryEvents']))
        public = order_data(order)
        self.assertNotIn('localDeliveryEvents', public)
        self.assertEqual(len(public['storeDelivery']['events']), 20)
        self.assertTrue(all(set(event) == {'action', 'requestKey', 'createdAt'}
                            for event in public['storeDelivery']['events']))

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
