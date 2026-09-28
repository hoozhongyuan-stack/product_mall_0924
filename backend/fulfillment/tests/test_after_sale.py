"""D0 refund capacity and fulfillment share the order transaction lock."""
import uuid
from unittest.mock import patch

from django.db import DatabaseError, transaction
from django.test import TransactionTestCase, override_settings

from fulfillment.models import Carrier, RedeemVoucher
from fulfillment.tests import test_flow
from payments.tests import test_refund_flow


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class AfterSaleFulfillmentTests(TransactionTestCase):
    setUp = test_refund_flow.RefundFlowTests.setUp
    case_and_intent = test_refund_flow.RefundFlowTests.case_and_intent
    refund_evidence = test_refund_flow.RefundFlowTests.refund_evidence
    _paid_order = test_flow.FulfillmentFlowTests._paid_order
    _order = test_flow.FulfillmentFlowTests._order
    _evidence = test_flow.FulfillmentFlowTests._evidence

    def test_refund_void_once_and_immutable_evidence(self):
        from fulfillment.service import void_refunded_quantity_locked
        from fulfillment.models import VoucherRefundEvent
        from payments.refunds import record_verified_refund
        order, line, case, intent = self.case_and_intent()
        self.assertEqual(record_verified_refund(self.refund_evidence(intent))["outcome"], "SUCCEEDED")
        first = VoucherRefundEvent.objects.get(case_id=case.id)
        with transaction.atomic():
            from orders.models import Order
            Order.objects.select_for_update().get(pk=order.pk)
            again = void_refunded_quantity_locked(line, 1, case.id)
        self.assertEqual(first.id, again.id)
        voucher = RedeemVoucher.objects.get(order_line=line)
        self.assertEqual(voucher.voided_quantity, 1)
        self.assertEqual(voucher.revision, 2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            VoucherRefundEvent.objects.filter(pk=first.pk).update(quantity=2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            VoucherRefundEvent.objects.filter(pk=first.pk).delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            RedeemVoucher.objects.filter(pk=voucher.pk).update(voided_quantity=2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            RedeemVoucher.objects.filter(pk=voucher.pk).update(voided_quantity=0)

    def test_void_requires_transaction_available_capacity_and_matching_replay(self):
        from fulfillment.service import FulfillmentError, redeem, void_refunded_quantity_locked
        order, line = self._paid_order()
        with self.assertRaises(RuntimeError):
            void_refunded_quantity_locked(line, 1, uuid.uuid4())
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.pk, self.owner, 1, voucher.revision, uuid.uuid4())
        with transaction.atomic():
            case_id = uuid.uuid4()
            void_refunded_quantity_locked(line, 1, case_id)
            with self.assertRaises(FulfillmentError):
                void_refunded_quantity_locked(line, 2, case_id)
            with self.assertRaises(FulfillmentError):
                void_refunded_quantity_locked(line, 1, uuid.uuid4())
            transaction.set_rollback(True)

    def test_held_quantity_excludes_redemption_but_preserves_remaining_read(self):
        from fulfillment.service import FulfillmentError, redeem, lookup_voucher
        from fulfillment.read import fulfillment_data
        from fulfillment.codes import voucher_code
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        with patch("aftersales.guards.reserved_unredeemed_quantity", return_value=1):
            with self.assertRaises(FulfillmentError) as error:
                redeem(voucher.pk, self.owner, 2, voucher.revision, uuid.uuid4())
            self.assertEqual(error.exception.code, "QUANTITY_EXCEEDED")
            data = lookup_voucher(voucher_code(line.id, voucher.nonce), self.owner)
            self.assertEqual((data["remainingQuantity"], data["heldQuantity"], data["availableQuantity"]), (2, 1, 1))
        with patch("aftersales.read.line_summaries", return_value={line.id: {
                "reservedQuantity": 1, "refundedQuantity": 0, "reservedUnredeemedQuantity": 1}}), \
                patch("aftersales.read.summaries", return_value={order.id: {
                    "activeCount": 1, "refundedQuantity": 0, "refundedFen": 0}}):
            data = fulfillment_data(order)
        self.assertEqual(data["fulfillmentStatus"], "AFTER_SALE")
        self.assertEqual(data["items"][str(line.id)]["availableQuantity"], 1)

    def test_shipping_guard_and_replay_boundary(self):
        from fulfillment.service import FulfillmentError, ship_order
        from aftersales.service import AfterSaleError
        order, _ = self._paid_order("SHIP")
        Carrier.objects.create(code="D0", name="合成承运商", enabled=True)
        key = uuid.uuid4()
        with patch("aftersales.guards.assert_shippable_locked", side_effect=AfterSaleError("售后占用", "AFTER_SALE_HOLD", 409)):
            with self.assertRaises(FulfillmentError):
                ship_order(order.pk, self.owner, "D0", "D0TRACK", order.revision, key)
        ship_order(order.pk, self.owner, "D0", "D0TRACK", order.revision, key)
        with patch("aftersales.guards.assert_shippable_locked", side_effect=AssertionError("replay must skip guard")):
            ship_order(order.pk, self.owner, "D0", "D0TRACK", order.revision, key)

    def test_used_refund_hold_blocks_redemption_reversal(self):
        from fulfillment.service import FulfillmentError, redeem, reverse_redemption
        from aftersales.service import AfterSaleError
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        used = redeem(voucher.pk, self.owner, 1, voucher.revision, uuid.uuid4())
        voucher.refresh_from_db()
        with patch("aftersales.guards.assert_reversal_allowed_locked", side_effect=AfterSaleError("已核销售后", "AFTER_SALE_HOLD", 409)):
            with self.assertRaises(FulfillmentError):
                reverse_redemption(used["eventId"], self.owner, "合成误核销", voucher.revision)
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 1)

    def test_full_refund_read_is_not_fulfillment_completed(self):
        from fulfillment.service import void_refunded_quantity_locked
        from fulfillment.read import fulfillment_data, page_fulfillment_summaries
        from payments.refunds import record_verified_refund
        order, line, _, intent = self.case_and_intent(quantity=2)
        self.assertEqual(record_verified_refund(self.refund_evidence(intent))["outcome"], "SUCCEEDED")
        detail = fulfillment_data(order)
        page = page_fulfillment_summaries([order])
        self.assertEqual(detail["fulfillmentStatus"], "REFUNDED")
        self.assertEqual(detail["items"][str(line.id)]["status"], "REFUNDED")
        self.assertEqual(page[order.id]["fulfillmentStatus"], "REFUNDED")

    def test_used_refund_read_reports_refunded_without_restoring_redemption(self):
        from fulfillment.service import redeem
        from fulfillment.read import fulfillment_data, page_fulfillment_summaries
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 2, voucher.revision, uuid.uuid4())
        summary = {order.id: {"activeCount": 0, "refundedQuantity": 2, "refundedFen": order.payable_fen}}
        lines = {line.id: {"reservedQuantity": 0, "reservedUnredeemedQuantity": 0, "refundedQuantity": 2}}
        with patch("aftersales.read.summaries", return_value=summary), \
                patch("aftersales.read.line_summaries", return_value=lines):
            self.assertEqual(fulfillment_data(order)["fulfillmentStatus"], "REFUNDED")
            self.assertEqual(page_fulfillment_summaries([order])[order.id]["fulfillmentStatus"], "REFUNDED")
        voucher.refresh_from_db()
        self.assertEqual((voucher.redeemed_quantity, voucher.voided_quantity), (2, 0))

    def test_historical_missing_voucher_fails_closed(self):
        from fulfillment.service import FulfillmentError, void_refunded_quantity_locked
        with patch("fulfillment.service.issue_paid_vouchers_locked"):
            _, line = self._paid_order()
        with transaction.atomic():
            with self.assertRaises(FulfillmentError) as error:
                void_refunded_quantity_locked(line, 1, uuid.uuid4())
        self.assertEqual(error.exception.code, "VOUCHER_MISSING")
