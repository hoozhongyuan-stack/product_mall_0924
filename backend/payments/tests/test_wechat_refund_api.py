"""D2 admin authorization, password binding, and provider callback HTTP limits."""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import TransactionTestCase, override_settings, Client
from accounts.models import AdminAccount
from payments.models import RefundIntent, RefundOperation
from payments.tests import test_wechat_refund_flow as flow
from payments.wechat_config import WechatGatewayError


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test", WECHAT_REFUND_ENABLED=True,
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class WechatRefundApiTests(TransactionTestCase):
    _order = flow.WechatRefundFlowTests._order
    _evidence = flow.WechatRefundFlowTests._evidence
    intent = flow.WechatRefundFlowTests.intent
    payload = flow.WechatRefundFlowTests.payload

    def setUp(self):
        flow.payment_flow.PaymentFlowTests.setUp(self)
        self.owner = AdminAccount.objects.get(login_name="payment-owner")
        self.gateway = Mock(config=SimpleNamespace(merchant_id="1900000001"))
        self.patch = patch("payments.wechat_refund_service.get_gateway", return_value=self.gateway)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.post('/api/v1/admin/auth/login', {'loginName': self.owner.login_name,
                                             'password': 'Long test password 2026!'})

    def post(self, path, body, **headers):
        return self.client.post(path, json.dumps(body), content_type="application/json", **headers)

    def confirm(self, case, action="refund.wechat.dispatch", revision=None):
        response = self.post('/api/v1/admin/auth/confirm', {'action': action, 'objectId': str(case.id),
            'revision': case.revision if revision is None else revision, 'password': 'Long test password 2026!'})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']['confirmationToken']

    def dispatch(self, intent, token='', **changes):
        return self.post(f'/api/v1/admin/aftersales/{intent.case_id}/wechat-refund',
            {'expectedRevision': intent.case.revision, **changes},
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()), HTTP_X_ACTION_CONFIRMATION=token)

    def test_dispatch_requires_password_and_amount_fields_rejected(self):
        intent = self.intent()
        self.assertEqual(self.dispatch(intent).status_code, 403)
        self.assertEqual(self.dispatch(intent, amountFen=1).status_code, 400)
        self.gateway.refund.assert_not_called()
        self.gateway.refund.return_value = self.payload(intent, "PROCESSING")
        response = self.dispatch(intent, self.confirm(intent.case))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response.json()['data']['status'], 'PROCESSING')

    def test_password_token_bound_to_action_case_revision_and_session(self):
        intent = self.intent()
        token = self.confirm(intent.case, action="aftersale.review")
        self.assertEqual(self.dispatch(intent, token).status_code, 403)
        token = self.confirm(intent.case, revision=intent.case.revision+1)
        self.assertEqual(self.dispatch(intent, token).status_code, 403)
        response = self.dispatch(intent, expectedRevision=intent.case.revision+1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(RefundOperation.objects.count(), 0)

    def test_unauthenticated_and_staff_without_funds_permission_rejected(self):
        intent = self.intent()
        url = f'/api/v1/admin/aftersales/{intent.case_id}/wechat-refund'
        self.assertEqual(Client().post(url, '{}', content_type='application/json').status_code, 401)
        staff = AdminAccount.objects.create_user('no-refund', 'Long test password 2026!',
            display_name='无退款权限', kind='STAFF', enabled=True)
        self.post('/api/v1/admin/auth/login', {'loginName': staff.login_name, 'password': 'Long test password 2026!'})
        self.assertEqual(self.dispatch(intent).status_code, 403)
        self.gateway.refund.assert_not_called()

    @override_settings(WECHAT_REFUND_ENABLED=False)
    def test_disabled_endpoint_no_provider_dispatch_and_no_confirmation_consumed(self):
        intent = self.intent()
        self.assertEqual(self.dispatch(intent, self.confirm(intent.case)).status_code, 409)
        self.gateway.refund.assert_not_called()
        self.assertEqual(RefundOperation.objects.count(), 0)

    def test_query_requires_empty_body_and_reuses_existing_intent(self):
        intent = self.intent()
        url = f'/api/v1/admin/aftersales/{intent.case_id}/wechat-refund/query'
        self.assertEqual(self.post(url, {'amountFen': 1000}).status_code, 400)
        self.gateway.query_refund.return_value = self.payload(intent)
        result = self.post(url, {})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data']['status'], 'SUCCEEDED')
        self.assertEqual(RefundIntent.objects.count(), 1)

    def test_callback_method_size_crypto_failure_and_valid_ack(self):
        from payments.wechat_refund_views import notification_view
        from django.test import RequestFactory
        factory = RequestFactory()
        self.assertEqual(notification_view(factory.get('/notify')).status_code, 405)
        self.assertEqual(notification_view(factory.post('/notify', b'x'*65537,
            content_type='application/json')).status_code, 413)
        self.gateway.verify_refund_notification.side_effect = WechatGatewayError('invalid', 'WECHAT_SIGNATURE_INVALID', 401)
        self.assertEqual(notification_view(factory.post('/notify', b'{}', content_type='application/json')).status_code, 401)
        with patch('payments.wechat_refund_service.handle_refund_notification', return_value={'outcome':'SUCCEEDED'}):
            self.assertEqual(notification_view(factory.post('/notify', b'{}', content_type='application/json')).status_code, 204)

    def test_query_rate_limit_no_more_provider_calls(self):
        intent = self.intent()
        self.gateway.query_refund.return_value = self.payload(intent, 'PROCESSING')
        url = f'/api/v1/admin/aftersales/{intent.case_id}/wechat-refund/query'
        for _ in range(30):
            self.assertEqual(self.post(url, {}).status_code, 200)
        self.assertEqual(self.post(url, {}).status_code, 429)
        self.assertEqual(self.gateway.query_refund.call_count, 30)
