"""Real PostgreSQL transactions prove money survives failed order settlement."""

import uuid
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount
from benefits.models import CouponCampaign, MemberCoupon, PointsAccount
from benefits.service import grant_points
from catalog.models import Asset, Category, MemberGrade, Product, Sku, SkuUnitVersion
from checkout.service import create_quote
from customers.models import Member
from inventory.models import InventoryBalance, InventoryLedger, InventoryReservation, Warehouse
from notifications.models import MessageTask
from orders.models import Order
from orders.service import cancel_order, submit_order
from payments.models import PaymentAnomaly, PaymentAnomalyEvent, PaymentEvent, PaymentReceipt
from payments.service import (PaymentError, VerifiedPayment, record_verified_payment,
                              settle_recorded_payment)
from shipping.models import ShippingPolicy


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class PaymentFlowTests(TransactionTestCase):
    def setUp(self):
        from payments.models import OfflinePaymentPolicy
        OfflinePaymentPolicy.objects.update_or_create(pk=1, defaults={
            'instructions': '测试付款说明', 'merchant_account_id': 'test-account'})
        ShippingPolicy.objects.get_or_create(pk=1, defaults={
            "fee_fen": 1000, "delivery_scope": "NATIONWIDE", "revision": 1})
        owner = AdminAccount.objects.create_user(
            "payment-owner", "Long test password 2026!", display_name="主账号", kind="OWNER")
        image = Asset.objects.create(kind=Asset.Kind.IMAGE, content_type="image/png", byte_size=1,
                                     width=1, height=1, sha256="8" * 64, original_name="test.png",
                                     stored_name="product/payment-test.png", created_by=owner)
        parent = Category.objects.create(name="酒类")
        child = Category.objects.create(name="白酒", parent=parent)
        with transaction.atomic():
            product = Product.objects.create(product_no="PAY-P", name="核销商品", category=child,
                                             fulfillment_kind="REDEEM", status="ON_SALE",
                                             redeem_valid_until=timezone.localdate()+timedelta(days=30),
                                             ever_on_sale=True, main_image=image)
            sku = Sku.objects.create(product=product, sku_code="PAY-SKU", spec_key="only",
                                     list_price_fen=1000, sale_status="ON_SALE")
            unit = SkuUnitVersion.objects.create(sku=sku, base_unit="件", sale_unit="件", ratio=1)
            sku.current_unit = unit
            sku.save(update_fields=["current_unit"])
            self.sku = sku
            self.balance = InventoryBalance.objects.create(
                warehouse=Warehouse.objects.create(code="PAY-W", name="默认仓", is_default=True),
                sku=sku, on_hand_base_units=10)
        grade = MemberGrade.objects.create(code="pay-grade", name="测试会员", rank=99)
        self.member = Member.objects.create(wechat_app_id="wx-payment-test",
                                            wechat_openid="pay-buyer", grade=grade)

    def _order(self, method="OFFLINE", coupon_id=None, points=0):
        body = {"items": [{"skuId": str(self.sku.id), "quantity": 2}]}
        if coupon_id:
            body["couponId"] = str(coupon_id)
        if points:
            body["pointsToUse"] = points
        quote = create_quote(body, self.member)
        data, _ = submit_order(self.member, {"quoteId": quote["quoteId"],
                                             "paymentMethod": method}, uuid.uuid4())
        return Order.objects.get(pk=data["orderId"])

    def _evidence(self, order, trade_no="BANK-001", amount=None, event_id="BANK-EVENT-001"):
        return VerifiedPayment(order_no=order.order_no, channel=order.payment_method,
                               merchant_account_id="test-account", external_trade_no=trade_no,
                               amount_fen=order.payable_fen if amount is None else amount,
                               paid_at=timezone.now(), source="OFFLINE_RECONCILIATION",
                               event_id=event_id)

    def test_verified_payment_settles_stock_and_benefits_once(self):
        now = timezone.now()
        campaign = CouponCampaign.objects.create(
            code="PAY-COUPON", title="测试券", kind="CASH", discount_fen=300,
            redeem_eligible=True, valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1))
        coupon = MemberCoupon.objects.create(member=self.member, campaign=campaign)
        grant_points(self.member, 200, now + timedelta(days=2), "payment-points")
        order = self._order(coupon_id=coupon.id, points=100)
        evidence = self._evidence(order)
        self.assertEqual(record_verified_payment(evidence)["outcome"], "PAID")
        self.assertEqual(record_verified_payment(evidence)["outcome"], "PAID")
        self.assertEqual(PaymentReceipt.objects.count(), 1)
        self.assertEqual(MessageTask.objects.filter(event_type="ORDER_PAID",
            source_id=PaymentReceipt.objects.get().id, member=self.member).count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.PAID)
        self.assertEqual(InventoryReservation.objects.get(order_line__order=order).status, "CONSUMED")
        self.assertEqual(InventoryLedger.objects.filter(order_line__order=order,
                                                        movement_type="SALE").count(), 1)
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.id).status, "USED")
        self.assertEqual(PointsAccount.objects.get(member=self.member).frozen_points, 0)

    def test_wrong_amount_and_closed_order_are_separate_anomalies(self):
        order = self._order()
        wrong = self._evidence(order, trade_no="BANK-WRONG", amount=order.payable_fen - 1,
                               event_id="BANK-WRONG-EVENT")
        self.assertEqual(record_verified_payment(wrong)["outcome"], "ANOMALY")
        self.assertEqual(PaymentAnomaly.objects.get().reason, "AMOUNT_MISMATCH")
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.PENDING_PAYMENT)
        cancel_order(self.member, order.id)
        late = self._evidence(order, trade_no="BANK-LATE", event_id="BANK-LATE-EVENT")
        self.assertEqual(record_verified_payment(late)["outcome"], "ANOMALY")
        self.assertEqual(PaymentAnomaly.objects.filter(reason="CLOSED_ORDER").count(), 1)
        self.assertEqual(PaymentReceipt.objects.count(), 2)
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.CLOSED)

    def test_verified_receipt_survives_settlement_failure_and_can_retry(self):
        order = self._order()
        evidence = self._evidence(order, trade_no="BANK-FAIL", event_id="BANK-FAIL-EVENT")
        with patch("orders.service.consume_order_reservations", side_effect=RuntimeError("test failure")):
            with self.assertRaises(PaymentError):
                record_verified_payment(evidence)
        self.assertEqual(PaymentReceipt.objects.count(), 1)
        self.assertEqual(PaymentAnomaly.objects.get().reason, "SETTLEMENT_FAILED")
        self.assertFalse(MessageTask.objects.filter(event_type="ORDER_PAID").exists())
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(settle_recorded_payment(PaymentReceipt.objects.get().id)["outcome"], "PAID")
        self.assertEqual(MessageTask.objects.filter(event_type="ORDER_PAID").count(), 1)
        self.assertEqual(PaymentAnomaly.objects.get().status, "RESOLVED")

    def test_expired_unclosed_order_is_closed_before_verified_money_is_applied(self):
        order = self._order()
        evidence = self._evidence(order, trade_no="BANK-EXPIRED")
        with patch("orders.service.timezone.now", return_value=order.expires_at + timedelta(seconds=1)):
            result = record_verified_payment(evidence)
        self.assertEqual(result["outcome"], "ANOMALY")
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.CLOSED)
        self.assertEqual(PaymentAnomaly.objects.get().reason, "CLOSED_ORDER")
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).reserved_base_units, 0)

    def test_external_trade_and_event_identities_cannot_be_reused_for_other_money(self):
        order = self._order()
        evidence = self._evidence(order)
        record_verified_payment(evidence)
        other = self._order()
        self.assertEqual(record_verified_payment(self._evidence(other))["outcome"], "ANOMALY")
        self.assertEqual(record_verified_payment(self._evidence(other, trade_no="BANK-002"))["outcome"],
                         "ANOMALY")
        self.assertEqual(PaymentReceipt.objects.count(), 2)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(PaymentAnomaly.objects.filter(reason="IDENTIFIER_CONFLICT").count(), 2)
        self.assertEqual(Order.objects.get(pk=other.id).status, Order.Status.PENDING_PAYMENT)

    def test_failed_settlement_then_cancel_reclassifies_exception_with_history(self):
        order = self._order()
        evidence = self._evidence(order)
        with patch("orders.service.consume_benefits", side_effect=RuntimeError("test failure")):
            with self.assertRaises(PaymentError):
                record_verified_payment(evidence)
        self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), 0)
        cancel_order(self.member, order.id)
        self.assertEqual(record_verified_payment(evidence)["outcome"], "ANOMALY")
        anomaly = PaymentAnomaly.objects.get()
        self.assertEqual(anomaly.reason, "CLOSED_ORDER")
        self.assertEqual(list(anomaly.history.order_by("created_at").values_list("reason", flat=True)),
                         ["SETTLEMENT_FAILED", "CLOSED_ORDER"])
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                PaymentAnomalyEvent.objects.filter(anomaly=anomaly).delete()

    def test_wrong_inventory_balance_identity_fails_closed_without_losing_money(self):
        order = self._order()
        wrong_balance = InventoryBalance.objects.create(
            warehouse=Warehouse.objects.create(code="OTHER-W", name="另一仓"), sku=self.sku,
            on_hand_base_units=10, reserved_base_units=2)
        InventoryReservation.objects.filter(order_line__order=order).update(balance=wrong_balance)
        with self.assertRaises(PaymentError):
            record_verified_payment(self._evidence(order))
        self.assertEqual(PaymentReceipt.objects.count(), 1)
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), 0)
        self.assertEqual(InventoryBalance.objects.get(pk=wrong_balance.pk).on_hand_base_units, 10)

    def test_wrong_benefit_member_identity_cannot_confirm_receipt(self):
        now = timezone.now()
        campaign = CouponCampaign.objects.create(
            code="WRONG-MEMBER", title="测试券", kind="CASH", discount_fen=300,
            redeem_eligible=True, valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1))
        coupon = MemberCoupon.objects.create(member=self.member, campaign=campaign)
        order = self._order(coupon_id=coupon.id)
        other = Member.objects.create(wechat_app_id="wx-payment-test", wechat_openid="other",
                                       grade=self.member.grade)
        # D4.1 rejects this corruption at the database boundary first. Retain
        # the original settlement defence test against pre-existing bad data.
        with self.assertRaises(DatabaseError), transaction.atomic():
            MemberCoupon.objects.filter(pk=coupon.id).update(member=other)
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.id).member_id, self.member.id)
        self.assertTrue(connection.settings_dict['NAME'].startswith('test_'))
        # Only this isolated test fixture bypasses the new guard; restore it
        # before exercising the real payment transaction and its assertions.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('ALTER TABLE benefit_member_coupon DISABLE TRIGGER benefit_coupon_entitlement_guard')
            try:
                MemberCoupon.objects.filter(pk=coupon.id).update(member=other)
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
            finally:
                cursor.execute('ALTER TABLE benefit_member_coupon ENABLE TRIGGER benefit_coupon_entitlement_guard')
        with self.assertRaises(PaymentError):
            record_verified_payment(self._evidence(order))
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), 0)
        self.assertEqual(PaymentReceipt.objects.count(), 1)

    def test_unknown_order_method_mismatch_and_extra_payment_are_recorded(self):
        order = self._order()
        evidence = self._evidence(order)
        unknown = replace(evidence, order_no="O-UNKNOWN", external_trade_no="UNKNOWN-001",
                          event_id="UNKNOWN-EVENT")
        self.assertEqual(record_verified_payment(unknown)["outcome"], "ANOMALY")
        wrong_method = replace(evidence, channel="WECHAT", source="WECHAT_QUERY",
                               external_trade_no="WECHAT-001", event_id="")
        self.assertEqual(record_verified_payment(wrong_method)["outcome"], "ANOMALY")
        self.assertEqual(record_verified_payment(evidence)["outcome"], "PAID")
        extra = replace(evidence, external_trade_no="BANK-EXTRA", event_id="BANK-EXTRA-EVENT")
        self.assertEqual(record_verified_payment(extra)["outcome"], "ANOMALY")
        self.assertEqual(set(PaymentAnomaly.objects.values_list("reason", flat=True)),
                         {"UNKNOWN_ORDER", "METHOD_MISMATCH", "ALREADY_PAID"})
        self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), 1)

    def test_invalid_or_unverified_source_cannot_create_a_money_fact(self):
        order = self._order()
        evidence = self._evidence(order)
        invalid = [None, replace(evidence, amount_fen=0), replace(evidence, amount_fen=True),
                   replace(evidence, merchant_account_id=""), replace(evidence, event_id=0),
                   replace(evidence, source="USER_REPORT"),
                   replace(evidence, channel="WECHAT", source="WECHAT_NOTIFICATION", event_id="")]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(PaymentError):
                record_verified_payment(item)
        self.assertEqual(PaymentReceipt.objects.count(), 0)

    def test_zero_value_order_is_paid_without_a_fictitious_receipt(self):
        self.sku.list_price_fen = 0
        self.sku.save(update_fields=["list_price_fen"])
        order = self._order()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(PaymentReceipt.objects.count(), 0)

    def test_recovery_waits_until_channel_event_registration_is_complete(self):
        order = self._order(method="WECHAT")
        evidence = VerifiedPayment(order_no=order.order_no, channel="WECHAT",
                                   merchant_account_id="test-merchant", external_trade_no="WX-WAIT",
                                   amount_fen=order.payable_fen, paid_at=timezone.now(),
                                   source="WECHAT_NOTIFICATION", event_id="WX-EVENT-WAIT")
        receipt = PaymentReceipt.objects.create(
            order=order, order_no=order.order_no, channel=evidence.channel,
            merchant_account_id=evidence.merchant_account_id,
            external_trade_no=evidence.external_trade_no, amount_fen=evidence.amount_fen,
            paid_at=evidence.paid_at, source=evidence.source)
        self.assertEqual(settle_recorded_payment(receipt.id)["outcome"], "PENDING")
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(record_verified_payment(evidence)["outcome"], "PAID")

    def test_payment_fact_is_database_immutable_and_nested_transaction_is_rejected(self):
        order = self._order()
        evidence = self._evidence(order)
        with transaction.atomic():
            with self.assertRaises(PaymentError) as nested:
                record_verified_payment(evidence)
        self.assertEqual(nested.exception.code, "PAYMENT_TRANSACTION_NESTED")
        record_verified_payment(evidence)
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                PaymentReceipt.objects.filter(order=order).update(amount_fen=1)
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                PaymentEvent.objects.all().delete()

    def test_database_rejects_two_applied_receipts_and_inconsistent_order_status(self):
        order = self._order()
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                Order.objects.filter(pk=order.id).update(status="PAID")
        evidence = self._evidence(order)
        record_verified_payment(evidence)
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                PaymentReceipt.objects.create(
                    order=order, order_no=order.order_no, channel="OFFLINE",
                    merchant_account_id="test-account", external_trade_no="SECOND-APPLIED",
                    amount_fen=order.payable_fen, paid_at=evidence.paid_at,
                    source="OFFLINE_RECONCILIATION", ready_for_settlement=True,
                    applied_at=timezone.now())
