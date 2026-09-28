"""D0 trusted refund evidence and failure recovery, entirely synthetic."""
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from threading import Event

from django.db import close_old_connections, transaction, DatabaseError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount, PermissionGroup, GroupPermission
from fulfillment.models import RedeemVoucher
from fulfillment.tests import test_flow as fulfillment_flow
from inventory.models import InventoryBalance, InventoryLedger
from aftersales.service import apply_case, review_case
from aftersales.models import AfterSaleCase, AfterSaleAllocation
from payments.refunds import (RefundError, VerifiedRefund, prepare_refund, begin_refund_operation,
                             finish_refund_operation, record_verified_refund, settle_recorded_refund,
                             recover_recorded_refunds)
from payments.models import RefundIntent, RefundEvidence, RefundAnomaly, RefundOperation, RefundHistory


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"OFFLINE": True, "WECHAT": True})
class RefundFlowTests(TransactionTestCase):
    _order = fulfillment_flow.FulfillmentFlowTests._order
    _evidence = fulfillment_flow.FulfillmentFlowTests._evidence
    _paid_order = fulfillment_flow.FulfillmentFlowTests._paid_order

    def setUp(self):
        fulfillment_flow.FulfillmentFlowTests.setUp(self)
        self.checker = AdminAccount.objects.create_user(login_name="refund-checker", display_name="合成复核员",
            password="Synthetic checker password 2026!", kind="STAFF", enabled=True)
        group = PermissionGroup.objects.create(code="refund_checker", name="合成退款复核")
        GroupPermission.objects.create(group=group, code="refund.offline.confirm")
        self.checker.permission_groups.add(group)

    def case_and_intent(self, kind="REDEEM", quantity=1):
        order, line = self._paid_order(kind)
        case = apply_case(self.member, line.id, "REFUND_ONLY", quantity, "合成退款申请", uuid.uuid4())
        if isinstance(case, tuple):
            case = case[0]
        case_id = case.id if hasattr(case, "id") else case["caseId"]
        case = AfterSaleCase.objects.get(pk=case_id)
        review_case(case.id, self.owner, True, "合成退款审核", case.revision)
        intent = prepare_refund(case.id, self.owner, uuid.uuid4())
        return order, line, case, intent

    def refund_evidence(self, intent):
        return VerifiedRefund(refund_no=intent.refund_no, channel="OFFLINE",
            merchant_account_id=intent.merchant_account_id,
            original_trade_no=intent.original_trade_no, external_refund_no="SYNTHETIC-" + uuid.uuid4().hex,
            amount_fen=intent.amount_fen, refunded_at=timezone.now(), source="OFFLINE_RECONCILIATION",
            confirmed_by_id=self.checker.id)

    def test_prepare_and_unknown_do_not_finish_or_void(self):
        _, line, case, intent = self.case_and_intent()
        replay = prepare_refund(case.id, self.owner, uuid.uuid4())
        self.assertEqual(replay.id, intent.id)
        op = begin_refund_operation(intent.id, "DISPATCH")
        finish_refund_operation(op.id, "UNKNOWN", "TIMEOUT")
        self.assertEqual(RefundIntent.objects.get(pk=intent.id).status, "UNKNOWN")
        self.assertEqual(AfterSaleCase.objects.get(pk=case.id).status, "WAITING_REFUND")
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity, 0)
        with self.assertRaises(RefundError):
            begin_refund_operation(intent.id, "DISPATCH")
        query = begin_refund_operation(intent.id, "QUERY")
        finish_refund_operation(query.id, "FAILED", "CHANNEL_REJECTED")
        retry = begin_refund_operation(intent.id, "DISPATCH")
        self.assertNotEqual(retry.id, op.id)
        self.assertEqual(RefundIntent.objects.count(), 1)

    def test_duplicate_success_voids_once_and_keeps_paid_fact(self):
        order, line, case, intent = self.case_and_intent()
        evidence = self.refund_evidence(intent)
        self.assertEqual(record_verified_refund(evidence)["outcome"], "SUCCEEDED")
        self.assertEqual(record_verified_refund(evidence)["outcome"], "SUCCEEDED")
        self.assertEqual(RefundEvidence.objects.count(), 1)
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity, 1)
        self.assertEqual(AfterSaleCase.objects.get(pk=case.id).status, "COMPLETED")
        order.refresh_from_db()
        self.assertEqual(order.status, "PAID")

    def test_receipt_survives_settlement_failure_and_retry(self):
        _, line, case, intent = self.case_and_intent()
        with patch("payments.refunds._finalize_case", side_effect=RuntimeError("synthetic fault")):
            result = record_verified_refund(self.refund_evidence(intent))
        self.assertEqual(result["outcome"], "SETTLEMENT_FAILED")
        receipt = RefundEvidence.objects.get()
        self.assertIsNone(receipt.applied_at)
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity, 0)
        with self.assertRaises(RefundError):
            begin_refund_operation(intent.id, "DISPATCH")
        self.assertEqual(RefundOperation.objects.count(), 0)
        self.assertEqual(settle_recorded_refund(receipt.id)["outcome"], "SUCCEEDED")
        self.assertEqual(settle_recorded_refund(receipt.id)["outcome"], "SUCCEEDED")
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity, 1)

    def test_mismatched_identity_or_amount_is_anomaly_not_success(self):
        _, line, _, intent = self.case_and_intent()
        evidence = replace(self.refund_evidence(intent), amount_fen=intent.amount_fen + 1)
        self.assertEqual(record_verified_refund(evidence)["outcome"], "ANOMALY")
        self.assertEqual(RefundAnomaly.objects.get().reason, "IDENTITY_MISMATCH")
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity, 0)

    def test_failure_recovery_does_not_downgrade_identity_anomaly(self):
        _, _, _, intent = self.case_and_intent()
        with patch("payments.refunds._finalize_case", side_effect=RuntimeError("synthetic fault")):
            record_verified_refund(self.refund_evidence(intent))
        row = RefundEvidence.objects.get()

        def racing_apply(_):
            RefundAnomaly.objects.filter(evidence=row).update(reason="IDENTIFIER_CONFLICT")
            raise RuntimeError("synthetic second fault")

        with patch("payments.refunds._apply", side_effect=racing_apply):
            self.assertEqual(settle_recorded_refund(row.id)["outcome"], "ANOMALY")
        self.assertEqual(RefundAnomaly.objects.get().reason, "IDENTIFIER_CONFLICT")
        self.assertEqual(settle_recorded_refund(row.id)["outcome"], "ANOMALY")

    def test_offline_confirmer_must_be_different_authorized_staff(self):
        _, _, _, intent = self.case_and_intent()
        with self.assertRaises(RefundError):
            record_verified_refund(replace(self.refund_evidence(intent), confirmed_by_id=self.owner.id))
        self.checker.enabled = False
        self.checker.save(update_fields=["enabled"])
        with self.assertRaises(RefundError):
            record_verified_refund(self.refund_evidence(intent))
        self.assertEqual(RefundEvidence.objects.count(), 0)

    def test_unshipped_success_restores_stock_once(self):
        _, line, _, intent = self.case_and_intent("SHIP")
        balance = InventoryBalance.objects.get(warehouse=line.warehouse, sku=line.sku)
        before = balance.on_hand_base_units
        evidence = self.refund_evidence(intent)
        record_verified_refund(evidence)
        record_verified_refund(evidence)
        balance.refresh_from_db()
        self.assertEqual(balance.on_hand_base_units, before + line.ratio)
        self.assertEqual(InventoryLedger.objects.filter(refund_case_id=intent.case_id).count(), 1)

    def test_inventory_refund_requires_valid_transaction_and_source(self):
        from inventory.refunds import restore_unshipped_refund_locked
        _, line, _, intent = self.case_and_intent("SHIP")
        with self.assertRaises(RuntimeError):
            restore_unshipped_refund_locked(line, 1, intent.case_id)
        for quantity, identity in ((True, intent.case_id), (0, intent.case_id), (1, "invalid")):
            with transaction.atomic(), self.assertRaises(ValueError):
                restore_unshipped_refund_locked(line, quantity, identity)
        _, redeem_line, _, redeem_intent = self.case_and_intent("REDEEM")
        with transaction.atomic(), self.assertRaises(ValueError):
            restore_unshipped_refund_locked(redeem_line, 1, redeem_intent.case_id)
        self.assertEqual(InventoryLedger.objects.filter(movement_type="REFUND").count(), 0)

    def test_inventory_refund_replay_checks_original_identity(self):
        from inventory.refunds import restore_unshipped_refund_locked
        _, line, _, intent = self.case_and_intent("SHIP")
        record_verified_refund(self.refund_evidence(intent))
        with transaction.atomic():
            original = restore_unshipped_refund_locked(line, 1, intent.case_id)
            self.assertEqual(original.refund_order_line_id, line.id)
        _, other_line, _, _ = self.case_and_intent("SHIP")
        with transaction.atomic(), self.assertRaises(ValueError):
            restore_unshipped_refund_locked(other_line, 1, intent.case_id)
        self.assertEqual(InventoryLedger.objects.filter(movement_type="REFUND").count(), 1)

    def test_parallel_success_applies_once(self):
        _, line, _, intent = self.case_and_intent()
        evidence = self.refund_evidence(intent)

        def confirm():
            close_old_connections()
            try:
                return record_verified_refund(evidence)["outcome"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual(outcomes, ["SUCCEEDED", "SUCCEEDED"])
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity, 1)

    def test_stale_failure_cannot_overwrite_success(self):
        _, _, _, intent = self.case_and_intent()
        op = begin_refund_operation(intent.id, "DISPATCH")
        record_verified_refund(self.refund_evidence(intent))
        finish_refund_operation(op.id, "FAILED", "LATE_TIMEOUT")
        self.assertEqual(RefundIntent.objects.get(pk=intent.id).status, "SUCCEEDED")

    def test_expired_dispatch_requires_query_and_stale_result_cannot_override(self):
        _, _, _, intent = self.case_and_intent()
        first = begin_refund_operation(intent.id, "DISPATCH")
        with self.assertRaises(RefundError):
            begin_refund_operation(intent.id, "QUERY")
        RefundIntent.objects.filter(pk=intent.id).update(lease_until=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(RefundError):
            begin_refund_operation(intent.id, "DISPATCH")
        query = begin_refund_operation(intent.id, "QUERY")
        finish_refund_operation(first.id, "FAILED", "LATE_RESULT")
        self.assertEqual(RefundIntent.objects.get(pk=intent.id).status, "PROCESSING")
        finish_refund_operation(query.id, "PROCESSING")
        self.assertEqual(finish_refund_operation(query.id, "FAILED"), "PROCESSING")

    def test_fund_identity_cannot_be_changed_or_deleted(self):
        _, _, _, intent = self.case_and_intent()
        record_verified_refund(self.refund_evidence(intent))
        row = RefundEvidence.objects.get()
        for operation in (
            lambda: RefundEvidence.objects.filter(pk=row.id).update(amount_fen=1),
            lambda: RefundIntent.objects.filter(pk=intent.id).update(status="FAILED", succeeded_at=None),
            lambda: RefundHistory.objects.all().delete(),
        ):
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()

    def test_second_funds_identity_for_completed_intent_is_anomaly(self):
        _, line, _, intent = self.case_and_intent()
        record_verified_refund(self.refund_evidence(intent))
        self.assertEqual(record_verified_refund(self.refund_evidence(intent))["outcome"], "ANOMALY")
        self.assertEqual(RefundAnomaly.objects.get().reason, "DUPLICATE_FUNDS")
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity, 1)

    def test_wechat_notification_dedup_and_conflict(self):
        from payments.service import VerifiedPayment, record_verified_payment
        from orders.models import Order, OrderLine
        order = self._order("WECHAT")
        # Synthetic trusted channel evidence, no provider or network request.
        record_verified_payment(VerifiedPayment(order_no=order.order_no, channel=order.payment_method,
            merchant_account_id="SYNTHETIC-MERCHANT", external_trade_no="SYNTHETIC-PAY",
            amount_fen=order.payable_fen, paid_at=timezone.now(), source="WECHAT_QUERY"))
        line = OrderLine.objects.get(order=order)
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "合成微信售后", uuid.uuid4())
        review_case(case.id, self.owner, True, "合成微信审核", case.revision)
        intent = prepare_refund(case.id, self.owner, uuid.uuid4())
        evidence = VerifiedRefund(refund_no=intent.refund_no, channel="WECHAT",
            merchant_account_id=intent.merchant_account_id, original_trade_no=intent.original_trade_no,
            external_refund_no="SYNTHETIC-WX-REFUND", amount_fen=intent.amount_fen,
            refunded_at=timezone.now(), source="WECHAT_NOTIFICATION", event_id="SYNTHETIC-EVENT")
        self.assertEqual(record_verified_refund(evidence)["outcome"], "SUCCEEDED")
        self.assertEqual(record_verified_refund(evidence)["outcome"], "SUCCEEDED")
        conflict = replace(evidence, external_refund_no="SYNTHETIC-WX-CONFLICT")
        self.assertEqual(record_verified_refund(conflict)["outcome"], "ANOMALY")

    def test_prepare_authority_key_and_state_validation(self):
        _, _, case, intent = self.case_and_intent()
        with self.assertRaises(RefundError):
            prepare_refund(case.id, self.checker, uuid.uuid4())
        with self.assertRaises(RefundError):
            prepare_refund(case.id, self.owner, "not-uuid")
        with self.assertRaises(RefundError):
            prepare_refund(uuid.uuid4(), self.owner, uuid.uuid4())
        second_order, line = self._paid_order()
        second = apply_case(self.member, line.id, "REFUND_ONLY", 1, "第二笔合成售后", uuid.uuid4())
        with self.assertRaises(RefundError):
            prepare_refund(second.id, self.owner, intent.request_key)
        with self.assertRaises(RefundError):
            prepare_refund(second.id, self.owner, uuid.uuid4())

    def test_invalid_evidence_and_outer_transaction_are_rejected(self):
        _, _, _, intent = self.case_and_intent()
        evidence = self.refund_evidence(intent)
        for bad in (None, replace(evidence, amount_fen=True), replace(evidence, source="WECHAT_QUERY"),
                    replace(evidence, channel="INVALID"), replace(evidence, refunded_at=datetime_naive()),
                    replace(evidence, refund_no="NO-SUCH-REFUND")):
            with self.assertRaises(RefundError):
                record_verified_refund(bad)
        with transaction.atomic(), self.assertRaises(RuntimeError):
            record_verified_refund(evidence)
        with transaction.atomic(), self.assertRaises(RuntimeError):
            settle_recorded_refund(uuid.uuid4())
        with self.assertRaises(RefundError):
            begin_refund_operation(intent.id, "INVALID")
        with self.assertRaises(RefundError):
            finish_refund_operation(uuid.uuid4(), "SUCCEEDED")

    def test_bounded_recovery_never_dispatches_and_keeps_anomalies_blocked(self):
        _, _, _, intent = self.case_and_intent()
        with patch("payments.refunds._finalize_case", side_effect=RuntimeError("synthetic fault")):
            record_verified_refund(self.refund_evidence(intent))
        with patch("payments.refunds.begin_refund_operation") as dispatch:
            self.assertEqual(recover_recorded_refunds(1), {"checked": 1, "succeeded": 1, "failed": 0, "anomalies": 0})
            self.assertEqual(recover_recorded_refunds(1)["checked"], 0)
            dispatch.assert_not_called()
        for invalid in (0, 501, True):
            with self.assertRaises(RefundError):
                recover_recorded_refunds(invalid)

    def test_two_orders_cannot_race_same_operator_refund_key(self):
        cases = []
        for _ in range(2):
            _, line = self._paid_order()
            case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "合成并发退款", uuid.uuid4())
            review_case(case.id, self.owner, True, "合成并发审核", case.revision)
            cases.append(case)
        key = uuid.uuid4()

        def prepare(case):
            close_old_connections()
            try:
                return str(prepare_refund(case.id, self.owner, key).id)
            except RefundError as exc:
                return exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(prepare, cases))
        self.assertEqual(results.count("IDEMPOTENCY_CONFLICT"), 1)
        self.assertEqual(RefundIntent.objects.count(), 1)

    def test_persisting_success_serializes_with_dispatch_decision(self):
        from payments.refunds import _ingest
        _, _, _, intent = self.case_and_intent()
        evidence = self.refund_evidence(intent)
        locked, release = Event(), Event()
        original_create = RefundEvidence.objects.get_or_create

        def pause_create(*args, **kwargs):
            locked.set()
            if not release.wait(3):
                raise RuntimeError("synthetic lock test timeout")
            return original_create(*args, **kwargs)

        def ingest():
            close_old_connections()
            try:
                return _ingest(evidence)
            finally:
                close_old_connections()

        def dispatch():
            close_old_connections()
            try:
                return begin_refund_operation(intent.id, "DISPATCH")
            except RefundError as exc:
                return exc.code
            finally:
                close_old_connections()

        with patch.object(RefundEvidence.objects, "get_or_create", side_effect=pause_create), \
             ThreadPoolExecutor(max_workers=2) as pool:
            funds = pool.submit(ingest)
            try:
                self.assertTrue(locked.wait(3))
                decision = pool.submit(dispatch)
                with self.assertRaises(FutureTimeoutError):
                    decision.result(timeout=0.15)
            finally:
                release.set()
            self.assertIsNotNone(funds.result(timeout=3))
            self.assertEqual(decision.result(timeout=3), "REFUND_SETTLEMENT_REQUIRED")
        self.assertEqual(RefundOperation.objects.count(), 0)


def datetime_naive():
    return timezone.now().replace(tzinfo=None)

    def test_refund_checker_revoked_between_validation_and_evidence_commit(self):
        _, _, _, intent = self.case_and_intent()
        evidence = self.refund_evidence(intent)
        from payments import refunds
        ingest = refunds._ingest
        def revoke_then_ingest(value):
            GroupPermission.objects.filter(group__accountgroup__account=self.checker, code='refund.offline.confirm').delete()
            return ingest(value)
        with patch('payments.refunds._ingest', side_effect=revoke_then_ingest):
            with self.assertRaises(RefundError) as error:
                record_verified_refund(evidence)
        self.assertEqual(error.exception.code, 'PERMISSION_DENIED')
        self.assertEqual(RefundEvidence.objects.count(), 0)
