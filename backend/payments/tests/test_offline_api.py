"""C1 public reports and authorized real-money reconciliation boundaries."""
import json
import uuid
from unittest.mock import patch
from django.db import DatabaseError, transaction
from django.utils import timezone
from accounts.models import AdminAccount
from customers.models import MemberSession
from orders.models import Order
from payments.models import OfflinePaymentPolicy, OfflinePaymentReport, OfflineReconciliation, PaymentReceipt
from payments.tests import test_payment_flow as flow
from django.test import TransactionTestCase, override_settings


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test", ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class OfflineApiTests(TransactionTestCase):
    _order = flow.PaymentFlowTests._order
    # Reuse fixtures, but do not repeat the inherited C0 tests in this suite.
    def setUp(self):
        flow.PaymentFlowTests.setUp(self)
        OfflinePaymentPolicy.objects.update_or_create(pk=1, defaults={
            'instructions': '测试资料：请核对收款账户后付款', 'merchant_account_id': 'TEST-BANK'})
        self.token, _ = MemberSession.issue(self.member)
        self.actor = AdminAccount.objects.get(login_name='payment-owner')
        self.login()

    def login(self):
        self.client.post('/api/v1/admin/auth/login', data=json.dumps({
            'loginName': self.actor.login_name, 'password': 'Long test password 2026!'}), content_type='application/json')

    def post(self, url, body, **headers):
        return self.client.post(url, data=json.dumps(body), content_type='application/json', **headers)

    def prepare(self, order, key=None, **changes):
        return self.post(f'/api/v1/admin/orders/{order.id}/offline-reconciliations', {
            'expectedRevision': order.revision, 'merchantAccountId': 'TEST-BANK',
            'externalTradeNo': 'TEST-TRADE', 'amountFen': order.payable_fen,
            'paidAt': timezone.now().isoformat(), 'note': '已核对测试到账记录', 'verified': True, **changes},
            HTTP_IDEMPOTENCY_KEY=str(key or uuid.uuid4()))

    def authorize(self, intent):
        r = self.post('/api/v1/admin/auth/confirm', {'action': 'payment.offline.confirm',
            'objectId': intent['reconciliationId'], 'revision': 1, 'password': 'Long test password 2026!'})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['data']['confirmationToken']

    def confirm(self, intent, token=''):
        return self.post(f"/api/v1/admin/payments/offline-reconciliations/{intent['reconciliationId']}/confirm", {},
                         HTTP_X_ACTION_CONFIRMATION=token)

    def test_c1_report_is_pending_owned_and_idempotent(self):
        o = self._order(); key = str(uuid.uuid4())
        url = f'/api/v1/app/orders/{o.id}/payment-report'
        r = self.post(url, {'note': '已转账，请核实'}, HTTP_AUTHORIZATION=f'Bearer {self.token}', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['data']['paymentReviewStatus'], 'PENDING_REVIEW')
        self.assertEqual(self.post(url, {'note': '已转账，请核实'}, HTTP_AUTHORIZATION=f'Bearer {self.token}', HTTP_IDEMPOTENCY_KEY=key).status_code, 200)
        self.assertEqual(self.post(url, {'note': '改动'}, HTTP_AUTHORIZATION=f'Bearer {self.token}', HTTP_IDEMPOTENCY_KEY=key).status_code, 409)
        self.assertEqual(Order.objects.get(pk=o.id).status, 'PENDING_PAYMENT')
        self.assertFalse(PaymentReceipt.objects.exists())
        self.assertEqual(OfflinePaymentReport.objects.count(), 1)

    def test_c1_requires_password_and_intent_keeps_evidence_immutable(self):
        o = self._order(); r = self.prepare(o)
        self.assertEqual(r.status_code, 201, r.content); intent = r.json()['data']
        self.assertFalse(PaymentReceipt.objects.exists())
        self.assertEqual(self.confirm(intent).status_code, 403)
        r = self.confirm(intent, self.authorize(intent))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['data']['outcome'], 'PAID')
        self.assertEqual(self.confirm(intent).json()['data']['outcome'], 'PAID')
        self.assertEqual(PaymentReceipt.objects.count(), 1)
        with transaction.atomic(), self.assertRaises(DatabaseError):
            OfflineReconciliation.objects.all().update(amount_fen=1)

    def test_c1_mismatch_and_closed_funds_are_kept_as_anomalies(self):
        o = self._order(); intent = self.prepare(o, amountFen=1).json()['data']
        r = self.confirm(intent, self.authorize(intent))
        self.assertEqual(r.json()['data']['outcome'], 'ANOMALY')
        self.assertEqual(Order.objects.get(pk=o.id).status, 'PENDING_PAYMENT')
        second = self.prepare(o, externalTradeNo='LATE').json()['data']
        from orders.service import cancel_order
        cancel_order(self.member, o.id)
        self.assertEqual(self.confirm(second, self.authorize(second)).json()['data']['outcome'], 'ANOMALY')
        self.assertEqual(PaymentReceipt.objects.count(), 2)

    def test_c1_failure_preserves_money_and_authorized_retry(self):
        intent = self.prepare(self._order()).json()['data']; token = self.authorize(intent)
        with patch('orders.service.consume_order_reservations', side_effect=RuntimeError('failure')):
            self.assertEqual(self.confirm(intent, token).status_code, 503)
        self.assertEqual(PaymentReceipt.objects.count(), 1)
        self.assertEqual(self.confirm(intent).json()['data']['outcome'], 'PAID')

    def test_c1_policy_revision_and_order_snapshot(self):
        o = self._order()
        policy = self.client.get('/api/v1/admin/payments/offline-policy').json()['data']
        policy.pop('configured'); policy.pop('availablePaymentMethods'); policy['expectedRevision'] = policy.pop('revision')
        policy['instructions'] = '新的付款说明'; policy['offlineTimeoutMinutes'] = 60
        r = self.client.put('/api/v1/admin/payments/offline-policy', data=json.dumps(policy), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        o.refresh_from_db(); self.assertEqual(o.payment_instructions['timeoutMinutes'], 1440)
        new = self._order(); self.assertEqual(new.payment_instructions['timeoutMinutes'], 60)
        self.assertEqual(self.client.put('/api/v1/admin/payments/offline-policy', data=json.dumps(policy), content_type='application/json').status_code, 409)

    def test_c1_unauthorized_actor_cannot_list_prepare_or_confirm(self):
        o = self._order(); intent = self.prepare(o).json()['data']; token = self.authorize(intent)
        staff = AdminAccount.objects.create_user('c1-staff', 'Long test password 2026!', display_name='无收款权限')
        self.actor = staff; self.login()
        self.assertEqual(self.client.get('/api/v1/admin/orders').status_code, 403)
        self.assertEqual(self.prepare(o).status_code, 403)
        self.assertEqual(self.confirm(intent, token).status_code, 403)
        self.assertFalse(PaymentReceipt.objects.exists())

    def test_c1_confirm_is_actor_bound_and_permissions_rechecked_on_retry(self):
        o = self._order(); intent = self.prepare(o).json()['data']; token = self.authorize(intent)
        self.assertEqual(self.confirm(intent, token).status_code, 200)
        original = self.actor
        other = AdminAccount.objects.create_user('c1-other-owner', 'Long test password 2026!', display_name='另一人')
        from accounts.models import AccountGroup, GroupPermission, PermissionGroup
        group = PermissionGroup.objects.create(code='c1-confirm', name='测试核对')
        GroupPermission.objects.create(group=group, code='payment.offline.confirm')
        GroupPermission.objects.create(group=group, code='order.read')
        AccountGroup.objects.create(account=other, group=group)
        self.actor = other; self.login()
        self.assertEqual(self.confirm(intent).status_code, 404)
        original.kind = 'STAFF'; original.save(update_fields=['kind'])
        self.actor = original; self.login()
        self.assertEqual(self.confirm(intent).status_code, 403)
        self.assertEqual(PaymentReceipt.objects.count(), 1)

    def test_c1_rejects_changed_input_stale_revision_and_wrong_bank(self):
        o = self._order(); key = uuid.uuid4(); body = {
            'expectedRevision': o.revision, 'merchantAccountId': 'TEST-BANK', 'externalTradeNo': 'TEST-TRADE',
            'amountFen': o.payable_fen, 'paidAt': timezone.now().isoformat(), 'note': '核对', 'verified': True}
        url = f'/api/v1/admin/orders/{o.id}/offline-reconciliations'
        self.assertEqual(self.post(url, body, HTTP_IDEMPOTENCY_KEY=str(key)).status_code, 201)
        self.assertEqual(self.post(url, body, HTTP_IDEMPOTENCY_KEY=str(key)).status_code, 200)
        self.assertEqual(self.post(url, {**body, 'amountFen': 1}, HTTP_IDEMPOTENCY_KEY=str(key)).status_code, 409)
        self.assertEqual(self.prepare(o, expectedRevision=999).status_code, 409)
        self.assertEqual(self.prepare(o, merchantAccountId='OTHER-BANK').status_code, 409)
        self.assertEqual(self.prepare(o, verified=False).status_code, 400)
        self.assertEqual(self.prepare(o, amountFen=True).status_code, 400)
        self.assertEqual(self.prepare(o, paidAt='invalid').status_code, 400)
        self.assertFalse(PaymentReceipt.objects.exists())

    def test_c1_report_cannot_access_other_member_closed_or_wechat_order(self):
        from customers.models import Member
        from orders.service import cancel_order
        o = self._order(); other = Member.objects.create(wechat_app_id='wx-payment-test', wechat_openid='another', grade=self.member.grade)
        other_token, _ = MemberSession.issue(other)
        def report(order, token):
            return self.post(f'/api/v1/app/orders/{order.id}/payment-report', {'note': '付款'},
                             HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(report(o, other_token).status_code, 404)
        cancel_order(self.member, o.id)
        self.assertEqual(report(o, self.token).status_code, 409)
        self.assertEqual(report(self._order(method='WECHAT'), self.token).status_code, 409)
        self.assertFalse(OfflinePaymentReport.objects.exists())

    def test_c1_order_lists_are_paginated_owned_and_do_not_leak_finance(self):
        o = self._order()
        intent = self.prepare(o).json()['data']
        member = self.client.get('/api/v1/app/orders?page=1&pageSize=1', HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(member.status_code, 200); data = member.json()['data']
        self.assertEqual((data['total'], len(data['items'])), (1,1))
        self.assertNotIn('confirmations', data['items'][0])
        self.assertEqual(self.client.get('/api/v1/admin/orders?pageSize=101').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/admin/orders?status=INVALID').status_code, 400)
        detail = self.client.get(f'/api/v1/admin/orders/{o.id}').json()['data']
        self.assertEqual(detail['confirmations'][0]['reconciliationId'], intent['reconciliationId'])
        self.assertEqual(detail['confirmations'][0]['actorId'], str(self.actor.id))
        self.assertEqual(detail['receipts'], [])

    def test_c1_csrf_required_for_admin_confirmation_and_policy(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.cookies = self.client.cookies
        self.assertEqual(strict.post('/api/v1/admin/payments/offline-policy', data='{}', content_type='application/json').status_code, 403)
        intent = self.prepare(self._order()).json()['data']
        self.assertEqual(strict.post(f"/api/v1/admin/payments/offline-reconciliations/{intent['reconciliationId']}/confirm", data='{}', content_type='application/json').status_code, 403)
        self.assertFalse(PaymentReceipt.objects.exists())

    def test_c1_order_replay_survives_disabled_method_and_snapshot_is_immutable(self):
        from django.test import override_settings
        from checkout.service import create_quote
        from orders.service import submit_order
        quote = create_quote({'items':[{'skuId':str(self.sku.id),'quantity':1}]}, self.member)
        body = {'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'}; key = uuid.uuid4()
        first, _ = submit_order(self.member, body, key)
        with override_settings(ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE':False,'WECHAT':False}):
            replay, repeated = submit_order(self.member, body, key)
        self.assertTrue(repeated); self.assertEqual(first['orderId'], replay['orderId'])
        with transaction.atomic(), self.assertRaises(DatabaseError):
            Order.objects.filter(pk=first['orderId']).update(payment_instructions={})

    def test_c1_parallel_confirmation_applies_once(self):
        from orders.tests.test_order_concurrency import OrderConcurrencyTests
        from django.test import Client
        intent = self.prepare(self._order()).json()['data']; token = self.authorize(intent)
        def work(index):
            client = Client(); client.cookies = self.client.cookies
            result = client.post(f"/api/v1/admin/payments/offline-reconciliations/{intent['reconciliationId']}/confirm", data='{}',
                content_type='application/json', HTTP_X_ACTION_CONFIRMATION=token)
            return result.status_code, result.json()
        results, errors = OrderConcurrencyTests._race(self, work)
        self.assertFalse(errors, errors)
        self.assertEqual([status for status, _ in results], [200, 200], results)
        self.assertEqual(PaymentReceipt.objects.count(),1)
        from inventory.models import InventoryLedger
        self.assertEqual(InventoryLedger.objects.filter(movement_type='SALE').count(),1)
