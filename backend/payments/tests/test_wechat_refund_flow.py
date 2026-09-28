"""D2 refund state races and synthetic channel evidence on isolated PostgreSQL."""
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from accounts.models import AdminAccount
from aftersales.models import AfterSaleCase
from aftersales.service import apply_case, review_case
from fulfillment.models import RedeemVoucher
from orders.models import OrderLine
from payments.models import RefundIntent, RefundEvidence, RefundOperation
from payments.service import record_verified_payment
from payments.refunds import prepare_refund, RefundError, recover_recorded_refunds
from payments.tests import test_payment_flow as payment_flow
from payments.wechat_config import WechatGatewayError
from payments.wechat_refund_service import (dispatch_intent, query_intent, handle_refund_notification,
    reconcile_wechat_refunds, refund_data)
from payments.wechat_refund_models import WechatRefundNotice


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test", WECHAT_REFUND_ENABLED=True,
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class WechatRefundFlowTests(TransactionTestCase):
    _order = payment_flow.PaymentFlowTests._order
    _evidence = payment_flow.PaymentFlowTests._evidence

    def setUp(self):
        payment_flow.PaymentFlowTests.setUp(self)
        self.owner = AdminAccount.objects.get(login_name="payment-owner")
        self.gateway = Mock(config=SimpleNamespace(merchant_id="1900000001"))
        self.patch = patch("payments.wechat_refund_service.get_gateway", return_value=self.gateway)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def intent(self):
        order = self._order("WECHAT")
        payment = replace(self._evidence(order, trade_no="WX-" + uuid.uuid4().hex),
            merchant_account_id="1900000001", source="WECHAT_QUERY", event_id="")
        record_verified_payment(payment)
        line = OrderLine.objects.get(order=order)
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "合成微信退款申请", uuid.uuid4())
        case_id = case["caseId"] if isinstance(case, dict) else case.id
        case = AfterSaleCase.objects.get(pk=case_id)
        review_case(case.id, self.owner, True, "合成退款审核", case.revision)
        return prepare_refund(case.id, self.owner, uuid.uuid4())

    def payload(self, intent, status="SUCCESS", **changes):
        return {"out_refund_no": intent.refund_no, "transaction_id": intent.original_trade_no,
            "out_trade_no": "SYNTHETIC-ORDER", "refund_id": "WX-REFUND-" + str(intent.id),
            "status": status, "mchid": "1900000001", "success_time": (intent.receipt.paid_at + timezone.timedelta(microseconds=1)).isoformat(),
            "amount": {"refund": intent.amount_fen, "total": intent.receipt.amount_fen, "currency": "CNY"}, **changes}

    def notify(self, intent, status="SUCCESS", event="notify-1", **changes):
        data = self.payload(intent, status, **changes)
        data = {**data, "refund_status": data.pop("status")}
        self.gateway.verify_refund_notification.return_value = (event, data)
        return handle_refund_notification({}, b"synthetic")

    def test_dispatch_acceptance_does_not_finish_even_if_status_success(self):
        intent = self.intent()
        self.gateway.refund.return_value = self.payload(intent)
        result = dispatch_intent(intent.id)
        self.assertEqual(result["status"], "PROCESSING")
        self.assertEqual(RefundEvidence.objects.count(), 0)
        self.assertEqual(AfterSaleCase.objects.get(pk=intent.case_id).status, "WAITING_REFUND")
        self.gateway.refund.assert_called_once_with(intent.refund_no, intent.original_trade_no,
            intent.amount_fen, intent.receipt.amount_fen)

    def test_timeout_keeps_stable_number_and_requires_query_before_retry(self):
        intent = self.intent()
        self.gateway.refund.side_effect = WechatGatewayError("timeout", "WECHAT_UNAVAILABLE", 503)
        with self.assertRaises(WechatGatewayError):
            dispatch_intent(intent.id)
        self.assertEqual(RefundIntent.objects.get(pk=intent.id).status, "UNKNOWN")
        with self.assertRaises(RefundError) as error:
            dispatch_intent(intent.id)
        self.assertEqual(error.exception.code, "REFUND_QUERY_REQUIRED")
        self.assertEqual(self.gateway.refund.call_count, 1)
        self.gateway.query_refund.side_effect = WechatGatewayError("not found", "WECHAT_REFUND_NOT_FOUND", 502)
        self.assertEqual(query_intent(intent.id)["status"], "FAILED")
        self.gateway.refund.side_effect = None
        self.gateway.refund.return_value = self.payload(intent, "PROCESSING")
        dispatch_intent(intent.id)
        self.assertEqual(self.gateway.refund.call_args.args[0], intent.refund_no)
        self.assertEqual(RefundIntent.objects.count(), 1)

    def test_query_success_and_duplicate_notification_finish_only_once(self):
        intent = self.intent()
        self.gateway.query_refund.return_value = self.payload(intent)
        self.assertEqual(query_intent(intent.id)["status"], "SUCCEEDED")
        self.assertEqual(self.notify(intent)["outcome"], "SUCCEEDED")
        self.assertEqual(self.notify(intent)["outcome"], "SUCCEEDED")
        self.assertEqual(RefundEvidence.objects.count(), 1)
        self.assertEqual(WechatRefundNotice.objects.count(), 1)
        self.assertEqual(RedeemVoucher.objects.get(order_line=intent.case.order_line).voided_quantity, 1)

    def test_closed_and_abnormal_do_not_release_occupation_or_downgrade_success(self):
        intent = self.intent()
        self.assertEqual(self.notify(intent, "CLOSED")["outcome"], "FAILED")
        self.assertFalse(refund_data(intent.case_id)["canDispatch"])
        self.assertEqual(self.notify(intent, "ABNORMAL", "event-2")["outcome"], "UNKNOWN")
        self.assertEqual(RefundEvidence.objects.count(), 0)
        self.assertEqual(self.notify(intent, event="event-3")["outcome"], "SUCCEEDED")
        self.assertEqual(self.notify(intent, "CLOSED", "event-4")["outcome"], "SUCCEEDED")

    def test_notification_event_conflict_rejected_without_second_money_fact(self):
        intent = self.intent()
        self.notify(intent)
        with self.assertRaises(RefundError) as caught:
            self.notify(intent, "CLOSED")
        self.assertEqual(caught.exception.code, "WECHAT_REFUND_EVENT_CONFLICT")
        self.assertTrue(WechatRefundNotice.objects.get().conflict)
        self.assertEqual(RefundEvidence.objects.count(), 1)

    def test_invalid_identity_amount_currency_time_and_merchant_never_settle(self):
        intent = self.intent()
        values = [{"transaction_id": "another-trade"}, {"out_refund_no": "another-refund"},
            {"mchid": "another-merchant"}, {"amount": {"refund": True, "total": 2000}},
            {"amount": {"refund": intent.amount_fen + 1, "total": 2000}},
            {"amount": {"refund": intent.amount_fen, "total": 1}},
            {"amount": {"refund": intent.amount_fen, "total": 2000, "currency": "USD"}},
            {"success_time": "2026-01-01"}]
        for changes in values:
            self.gateway.query_refund.return_value = self.payload(intent, **changes)
            with self.subTest(changes=changes), self.assertRaises(RefundError):
                query_intent(intent.id)
        self.assertEqual(RefundEvidence.objects.count(), 0)

    def test_funds_survive_finalization_failure_and_recovery_never_dispatches(self):
        intent = self.intent()
        self.gateway.query_refund.return_value = self.payload(intent)
        with patch("payments.refunds._finalize_case", side_effect=RuntimeError("synthetic fault")):
            self.assertEqual(query_intent(intent.id)["status"], "UNKNOWN")
        self.assertEqual(RefundEvidence.objects.count(), 1)
        self.assertFalse(refund_data(intent.case_id)["canDispatch"])
        recover_recorded_refunds()
        self.assertEqual(RefundIntent.objects.get(pk=intent.id).status, "SUCCEEDED")
        self.gateway.refund.assert_not_called()

    def test_provider_io_is_outside_transaction_and_atomic_caller_rejected(self):
        intent = self.intent()
        def response(*args):
            self.assertFalse(connection.in_atomic_block)
            return self.payload(intent, "PROCESSING")
        self.gateway.refund.side_effect = response
        dispatch_intent(intent.id)
        with transaction.atomic(), self.assertRaises(RuntimeError):
            query_intent(intent.id)

    @override_settings(WECHAT_REFUND_ENABLED=False)
    def test_disabled_refunds_no_gateway_no_money_no_operation(self):
        intent = self.intent()
        with self.assertRaises(RefundError):
            dispatch_intent(intent.id)
        with self.assertRaises(RefundError):
            query_intent(intent.id)
        self.gateway.refund.assert_not_called()
        self.gateway.query_refund.assert_not_called()
        self.assertEqual(RefundOperation.objects.count(), 0)
        self.assertFalse(refund_data(intent.case_id)["available"])

    def test_reconcile_bounded_query_only_skips_recent_and_never_dispatches(self):
        intent = self.intent()
        self.gateway.refund.return_value = self.payload(intent, "PROCESSING")
        dispatch_intent(intent.id)
        self.gateway.query_refund.return_value = self.payload(intent)
        self.assertEqual(reconcile_wechat_refunds(2)["checked"], 0)
        with patch("payments.wechat_refund_service.timezone.now", return_value=timezone.now() + timezone.timedelta(minutes=2)):
            self.assertEqual(reconcile_wechat_refunds(2)["succeeded"], 1)
        self.assertEqual(self.gateway.refund.call_count, 1)
        with self.assertRaises(RefundError):
            reconcile_wechat_refunds(501)

    def test_duplicate_non_success_does_not_clear_new_lease_or_duplicate_history(self):
        from payments.refunds import begin_refund_operation
        from payments.models import RefundHistory
        intent = self.intent()
        self.notify(intent, "ABNORMAL")
        before = RefundHistory.objects.count()
        operation = begin_refund_operation(intent.id, "QUERY")
        self.notify(intent, "ABNORMAL")
        row = RefundIntent.objects.get(pk=intent.id)
        self.assertEqual(row.active_operation_id, operation.id)
        self.assertEqual(RefundHistory.objects.count(), before)

    def test_cross_intent_event_collision_blocks_both_without_money(self):
        first, second = self.intent(), self.intent()
        self.notify(first, "ABNORMAL")
        with self.assertRaises(RefundError):
            self.notify(second, "ABNORMAL")
        for intent in [first, second]:
            self.assertEqual(refund_data(intent.case_id)["failureCode"], "WECHAT_REFUND_EVENT_CONFLICT")
            self.assertFalse(refund_data(intent.case_id)["canQuery"])
            with self.assertRaises(RefundError):
                query_intent(intent.id)
            with self.assertRaises(RefundError):
                self.notify(intent, event="new-event")
        self.assertEqual(RefundEvidence.objects.count(), 0)

    def test_notice_timestamp_conflict_and_database_guards_cannot_rewrite_facts(self):
        from django.db import DatabaseError
        from payments.wechat_refund_models import WechatRefundConflict
        intent = self.intent()
        self.notify(intent, "ABNORMAL")
        with self.assertRaises(RefundError):
            self.notify(intent, "ABNORMAL", success_time=timezone.now().isoformat())
        row = WechatRefundNotice.objects.get()
        for changes in [{"digest": "0" * 64}, {"provider_status": "SUCCESS"}, {"conflict": False},
                        {"processed_at": None}]:
            with transaction.atomic(), self.assertRaises(DatabaseError):
                WechatRefundNotice.objects.filter(pk=row.id).update(**changes)
        with transaction.atomic(), self.assertRaises(DatabaseError):
            WechatRefundNotice.objects.filter(pk=row.id).delete()
        with transaction.atomic(), self.assertRaises(DatabaseError):
            WechatRefundConflict.objects.all().delete()

    def test_disabled_actor_rechecked_before_actual_dispatch_and_query(self):
        intent = self.intent()
        AdminAccount.objects.filter(pk=self.owner.pk).update(enabled=False)
        for action in [dispatch_intent, query_intent]:
            with self.assertRaises(RefundError) as error:
                action(intent.id, self.owner.id)
            self.assertEqual(error.exception.code, "PERMISSION_DENIED")
        self.assertEqual(RefundOperation.objects.count(), 0)
        self.gateway.refund.assert_not_called()
        self.gateway.query_refund.assert_not_called()

    def test_notification_success_wins_dispatch_result_and_repeat_query_no_network(self):
        intent = self.intent()
        def racing_result(*args):
            self.notify(intent)
            return self.payload(intent, "PROCESSING")
        self.gateway.refund.side_effect = racing_result
        self.assertEqual(dispatch_intent(intent.id)["status"], "SUCCEEDED")
        self.assertEqual(RefundOperation.objects.get().outcome, "STALE")
        self.assertEqual(query_intent(intent.id)["status"], "SUCCEEDED")
        self.gateway.query_refund.assert_not_called()

    def test_missing_configuration_no_operation_and_safe_dto(self):
        intent = self.intent()
        with patch("payments.wechat_refund_service.get_gateway", side_effect=WechatGatewayError(
                "not configured", "WECHAT_NOT_CONFIGURED", 503)):
            self.assertFalse(refund_data(intent.case_id)["available"])
            with self.assertRaises(WechatGatewayError):
                dispatch_intent(intent.id)
        self.assertEqual(RefundOperation.objects.count(), 0)

    def test_internal_error_unknown_and_merchant_change_never_dispatch(self):
        intent = self.intent()
        self.gateway.config.merchant_id = "other-merchant"
        with self.assertRaises(RefundError):
            dispatch_intent(intent.id)
        self.gateway.refund.assert_not_called()
        self.gateway.config.merchant_id = intent.merchant_account_id
        self.gateway.refund.side_effect = RuntimeError("private database details")
        with self.assertRaises(RefundError) as error:
            dispatch_intent(intent.id)
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(RefundIntent.objects.get(pk=intent.pk).status, "UNKNOWN")

    @override_settings(WECHAT_REFUND_ENABLED=False)
    def test_disabled_reconcile_skips_without_gateway(self):
        self.assertEqual(reconcile_wechat_refunds(10), {"checked": 0, "succeeded": 0, "pending": 0,
                                                     "failed": 0, "skipped": 1})
        self.gateway.query_refund.assert_not_called()

    def test_reconcile_failure_bounded_and_no_dispatch(self):
        intent = self.intent()
        self.gateway.refund.side_effect = WechatGatewayError("timeout", "WECHAT_UNAVAILABLE", 503)
        with self.assertRaises(WechatGatewayError):
            dispatch_intent(intent.id)
        with patch("payments.wechat_refund_service.timezone.now", return_value=timezone.now() + timezone.timedelta(minutes=2)):
            self.gateway.query_refund.side_effect = WechatGatewayError("timeout", "WECHAT_UNAVAILABLE", 503)
            self.assertEqual(reconcile_wechat_refunds(1)["failed"], 1)
        self.assertEqual(self.gateway.refund.call_count, 1)

    def test_event_conflict_blocks_shared_recovery_of_committed_money(self):
        from payments.models import RefundAnomaly
        intent = self.intent()
        with patch('payments.refunds._finalize_case', side_effect=RuntimeError('synthetic fault')):
            self.assertEqual(self.notify(intent)['outcome'], 'SETTLEMENT_FAILED')
        self.assertEqual(RefundEvidence.objects.count(), 1)
        with self.assertRaises(RefundError):
            self.notify(intent, 'CLOSED')
        result = recover_recorded_refunds()
        self.assertEqual(result['anomalies'], 1)
        self.assertEqual(RefundAnomaly.objects.get().reason, 'REFUND_EVENT_CONFLICT')
        self.assertEqual(AfterSaleCase.objects.get(pk=intent.case_id).status, 'WAITING_REFUND')
        self.assertEqual(RedeemVoucher.objects.get(order_line=intent.case.order_line).voided_quantity, 0)
        self.assertEqual(recover_recorded_refunds()['checked'], 0)

    def test_query_original_attempt_currency_and_success_time_are_strict(self):
        from payments.models import WechatPaymentAttempt
        intent = self.intent()
        for changes in [{'success_time': (intent.receipt.paid_at-timezone.timedelta(seconds=1)).isoformat()},
                        {'success_time': (timezone.now()+timezone.timedelta(minutes=1)).isoformat()},
                        {'amount': {'refund': intent.amount_fen, 'total': intent.receipt.amount_fen}}]:
            self.gateway.query_refund.return_value = self.payload(intent, **changes)
            with self.assertRaises(RefundError):
                query_intent(intent.id)
        # Real C2 attempts bind provider merchant order identity as well as transaction_id.
        with patch('payments.wechat_refund_service.WechatPaymentAttempt.objects.filter') as found:
            found.return_value.first.return_value = SimpleNamespace(out_trade_no='BOUND-ORDER')
            self.gateway.query_refund.return_value = self.payload(intent)
            with self.assertRaises(RefundError):
                query_intent(intent.id)
        self.assertEqual(RefundEvidence.objects.count(), 0)

    def test_real_ephemeral_rsa_aes_callback_reaches_shared_funds_boundary(self):
        import base64
        import json
        import time
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from payments.wechat_config import WechatConfig
        from payments.wechat_refund_gateway import WechatRefundGateway
        from payments.wechat_refund_views import notification_view
        from django.test import RequestFactory
        intent = self.intent()
        merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        provider_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        config = WechatConfig(app_id='wxRefundTest', merchant_id=intent.merchant_account_id,
            merchant_serial='ABCD', notify_url='https://shop.example.test/payment', api_v3_key=b's'*32,
            private_key_pem=merchant_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()), platform_keys=(('PUB_KEY_ID_123', provider_key.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)),))
        payload = self.payload(intent)
        payload = {k: v for k, v in payload.items() if k != 'status'}
        payload = {**payload, 'refund_status': 'SUCCESS'}
        cipher = AESGCM(config.api_v3_key).encrypt(b'123456789012', json.dumps(payload).encode(), b'refund')
        envelope = {'id': 'signed-refund-event', 'event_type': 'REFUND.SUCCESS', 'resource_type': 'encrypt-resource',
            'resource': {'original_type': 'refund', 'algorithm': 'AEAD_AES_256_GCM', 'nonce': '123456789012',
                'associated_data': 'refund', 'ciphertext': base64.b64encode(cipher).decode()}}
        raw = json.dumps(envelope).encode()
        stamp = str(int(time.time()))
        signature = provider_key.sign(stamp.encode()+b'\nnonce\n'+raw+b'\n', padding.PKCS1v15(), hashes.SHA256())
        headers = {'HTTP_WECHATPAY_TIMESTAMP': stamp, 'HTTP_WECHATPAY_NONCE': 'nonce',
            'HTTP_WECHATPAY_SERIAL': 'PUB_KEY_ID_123', 'HTTP_WECHATPAY_SIGNATURE': base64.b64encode(signature).decode()}
        gateway = WechatRefundGateway(config, transport=Mock(side_effect=AssertionError('network forbidden')),
            refund_notify_url='https://shop.example.test/refund')
        with patch('payments.wechat_refund_service.get_gateway', return_value=gateway):
            for _ in range(2):
                request = RequestFactory().post('/notify', raw, content_type='application/json', **headers)
                self.assertEqual(notification_view(request).status_code, 204)
        self.assertEqual(RefundEvidence.objects.count(), 1)
        self.assertEqual(AfterSaleCase.objects.get(pk=intent.case_id).status, 'COMPLETED')
        gateway.transport.assert_not_called()
