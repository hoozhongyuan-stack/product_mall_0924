"""Independent PostgreSQL connections exercise the stock and request locks."""

import threading
import uuid
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount
from catalog.models import Asset, Category, MemberGrade, Product, Sku, SkuUnitVersion
from checkout.service import create_quote
from customers.models import Member
from inventory.models import (InventoryBalance, InventoryLedger, InventoryReservationEvent,
                              StockPool, StockPoolSku, Warehouse)
from inventory.outbound_service import confirm_outbound, create_outbound
from inventory.validation import InventoryError
from orders.models import Order, OrderIdempotency
from orders.service import OrderError, cancel_order, close_expired_orders, submit_order
from payments.models import PaymentAnomaly, PaymentReceipt
from payments.service import (PaymentError, VerifiedPayment, record_verified_payment,
                              settle_recorded_payment)
from shipping.models import ShippingPolicy


@override_settings(WECHAT_MINI_APP_ID="wx-order-concurrent",
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class OrderConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from payments.models import OfflinePaymentPolicy
        OfflinePaymentPolicy.objects.update_or_create(pk=1, defaults={
            'instructions': '测试付款说明', 'merchant_account_id': 'test-account'})
        # TransactionTestCase flushes data migration rows between methods.
        ShippingPolicy.objects.get_or_create(pk=1, defaults={
            "fee_fen": 1000, "delivery_scope": "NATIONWIDE", "revision": 1})
        owner = AdminAccount.objects.create_user(
            "order-concurrent-owner", "Long test password 2026!", display_name="主账号", kind="OWNER")
        self.owner = owner
        image = Asset.objects.create(kind=Asset.Kind.IMAGE, content_type="image/png", byte_size=1,
                                     width=1, height=1, sha256="2" * 64, original_name="test.png",
                                     stored_name="product/test.png", created_by=owner)
        parent = Category.objects.create(name="酒类")
        child = Category.objects.create(name="白酒", parent=parent)
        warehouse = Warehouse.objects.create(code="CON-W", name="默认仓", is_default=True)
        self.warehouse = warehouse
        grade = MemberGrade.objects.create(code="con-grade", name="并发会员", rank=99)
        self.members = [Member.objects.create(wechat_app_id="wx-order-concurrent",
                                              wechat_openid=f"buyer-{i}", grade=grade) for i in range(2)]
        self.skus = []
        with transaction.atomic():
            product = Product.objects.create(product_no="CON-P", name="核销商品", category=child,
                                             fulfillment_kind="REDEEM", status="ON_SALE",
                                             redeem_valid_until=timezone.localdate()+timedelta(days=30),
                                             ever_on_sale=True, main_image=image)
            for i in range(2):
                sku = Sku.objects.create(product=product, sku_code=f"CON-S{i}", spec_key=f"S{i}",
                                         list_price_fen=1000, sale_status="ON_SALE")
                unit = SkuUnitVersion.objects.create(sku=sku, base_unit="件", sale_unit="件", ratio=1)
                sku.current_unit = unit
                sku.save(update_fields=["current_unit"])
                InventoryBalance.objects.create(warehouse=warehouse, sku=sku, on_hand_base_units=3)
                self.skus.append(sku)

    def _quote(self, member, ids, quantity):
        return create_quote({"items": [{"skuId": str(sku_id), "quantity": quantity}
                                       for sku_id in ids]}, member)["quoteId"]

    def _race(self, work):
        gate = threading.Barrier(2)
        results, errors = [], []

        def run(index):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                results.append(work(index))
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads), "订单锁发生死锁")
        return results, errors

    def test_two_buyers_cannot_oversell_same_sku(self):
        quotes = [self._quote(member, [self.skus[0].id], 2) for member in self.members]
        keys = [uuid.uuid4(), uuid.uuid4()]
        results, errors = self._race(lambda i: submit_order(
            Member.objects.select_related("grade").get(pk=self.members[i].id),
            {"quoteId": quotes[i], "paymentMethod": "OFFLINE"}, keys[i]))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OrderError)
        self.assertEqual(errors[0].code, "OUT_OF_STOCK")
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 2)
        self.assertEqual(Order.objects.count(), 1)

    def test_two_buyers_cannot_oversell_shared_pool_across_skus(self):
        InventoryBalance.objects.filter(sku=self.skus[1]).delete()
        pool = StockPool.objects.create(anchor_sku=self.skus[0], base_unit="件")
        StockPoolSku.objects.bulk_create([
            StockPoolSku(pool=pool, sku=sku) for sku in self.skus
        ])
        quotes = [self._quote(self.members[i], [self.skus[i].id], 2) for i in range(2)]
        results, errors = self._race(lambda i: submit_order(
            Member.objects.select_related("grade").get(pk=self.members[i].id),
            {"quoteId": quotes[i], "paymentMethod": "OFFLINE"}, uuid.uuid4()))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OrderError)
        self.assertEqual(errors[0].code, "OUT_OF_STOCK")
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 2)
        self.assertEqual(Order.objects.count(), 1)

    def test_same_key_concurrent_replay_creates_one_order(self):
        quote = self._quote(self.members[0], [self.skus[0].id], 1)
        key = uuid.uuid4()
        results, errors = self._race(lambda i: submit_order(
            Member.objects.select_related("grade").get(pk=self.members[0].id),
            {"quoteId": quote, "paymentMethod": "OFFLINE"}, key))
        self.assertFalse(errors, errors)
        self.assertEqual(sorted(replayed for _, replayed in results), [False, True])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderIdempotency.objects.count(), 1)
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 1)

    def test_reverse_quote_line_order_uses_same_balance_lock_order(self):
        ids = [sku.id for sku in self.skus]
        quotes = [self._quote(self.members[0], ids, 1),
                  self._quote(self.members[1], list(reversed(ids)), 1)]
        results, errors = self._race(lambda i: submit_order(
            Member.objects.select_related("grade").get(pk=self.members[i].id),
            {"quoteId": quotes[i], "paymentMethod": "OFFLINE"}, uuid.uuid4()))
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(list(InventoryBalance.objects.order_by("sku_id").values_list(
            "reserved_base_units", flat=True)), [2, 2])

    def test_cancel_and_timeout_race_release_once(self):
        quote = self._quote(self.members[0], [self.skus[0].id], 2)
        data, _ = submit_order(self.members[0], {"quoteId": quote, "paymentMethod": "OFFLINE"}, uuid.uuid4())
        order_id = uuid.UUID(data["orderId"])
        future = timezone.now() + timedelta(hours=25)
        with patch("orders.service.timezone.now", return_value=future):
            results, errors = self._race(lambda i: cancel_order(self.members[0], order_id)
                                         if i == 0 else close_expired_orders())
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(Order.objects.get(pk=order_id).status, Order.Status.CLOSED)
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 0)
        self.assertEqual(InventoryReservationEvent.objects.filter(action="RELEASE").count(), 1)

    def test_order_and_manual_outbound_share_available_stock_without_deadlock(self):
        sku = self.skus[0]
        quote = self._quote(self.members[0], [sku.id], 2)
        request = SimpleNamespace(request_id=uuid.uuid4())
        draft = create_outbound(request, self.owner, {
            "warehouseId": str(self.warehouse.id), "reason": "SAMPLE", "note": "并发验证",
            "items": [{"skuId": str(sku.id), "quantity": 2, "unit": "BASE"}],
        }, uuid.uuid4())

        def work(index):
            if index == 0:
                return submit_order(self.members[0], {
                    "quoteId": quote, "paymentMethod": "OFFLINE"}, uuid.uuid4())
            return confirm_outbound(SimpleNamespace(request_id=uuid.uuid4()), self.owner,
                                    uuid.UUID(draft["outboundId"]), 1, uuid.uuid4())

        results, errors = self._race(work)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], (OrderError, InventoryError))
        balance = InventoryBalance.objects.get(sku=sku)
        self.assertEqual(balance.on_hand_base_units - balance.reserved_base_units, 1)
        self.assertEqual(Order.objects.count() + InventoryLedger.objects.count(), 1)

    def test_payment_and_cancel_race_has_one_final_stock_transition(self):
        quote = self._quote(self.members[0], [self.skus[0].id], 2)
        data, _ = submit_order(self.members[0], {"quoteId": quote, "paymentMethod": "OFFLINE"},
                               uuid.uuid4())
        order = Order.objects.get(pk=data["orderId"])
        evidence = VerifiedPayment(order_no=order.order_no, channel="OFFLINE",
                                   merchant_account_id="concurrent-bank", external_trade_no="RACE-001",
                                   amount_fen=order.payable_fen, paid_at=timezone.now(),
                                   source="OFFLINE_RECONCILIATION")
        results, errors = self._race(lambda i: record_verified_payment(evidence) if i == 0
                                     else cancel_order(self.members[0], order.id))
        self.assertEqual(len(PaymentReceipt.objects.all()), 1)
        self.assertEqual(len(results) + len(errors), 2)
        order.refresh_from_db()
        balance = InventoryBalance.objects.get(sku=self.skus[0])
        if order.status == Order.Status.PAID:
            self.assertEqual((balance.on_hand_base_units, balance.reserved_base_units), (1, 0))
            self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), 1)
            self.assertEqual(PaymentAnomaly.objects.count(), 0)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], OrderError)
        else:
            self.assertEqual(order.status, Order.Status.CLOSED)
            self.assertEqual((balance.on_hand_base_units, balance.reserved_base_units), (3, 0))
            self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), 0)
            self.assertEqual(PaymentAnomaly.objects.get().reason, "CLOSED_ORDER")
            self.assertFalse(errors, errors)

    def test_payment_and_expiry_race_keeps_late_money_as_anomaly(self):
        quote = self._quote(self.members[0], [self.skus[0].id], 1)
        data, _ = submit_order(self.members[0], {"quoteId": quote, "paymentMethod": "OFFLINE"},
                               uuid.uuid4())
        order = Order.objects.get(pk=data["orderId"])
        evidence = VerifiedPayment(order_no=order.order_no, channel="OFFLINE",
                                   merchant_account_id="concurrent-bank", external_trade_no="RACE-LATE",
                                   amount_fen=order.payable_fen, paid_at=timezone.now(),
                                   source="OFFLINE_RECONCILIATION")
        with patch("orders.service.timezone.now", return_value=order.expires_at + timedelta(seconds=1)):
            results, errors = self._race(lambda i: record_verified_payment(evidence)
                                         if i == 0 else close_expired_orders())
        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.CLOSED)
        self.assertEqual(PaymentAnomaly.objects.get().reason, "CLOSED_ORDER")
        self.assertEqual(InventoryReservationEvent.objects.filter(action="RELEASE").count(), 1)
        self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), 0)

    def test_stale_settlement_failure_cannot_overwrite_new_identity_conflict(self):
        from payments.service import _record_failure

        quote = self._quote(self.members[0], [self.skus[0].id], 1)
        data, _ = submit_order(self.members[0], {"quoteId": quote, "paymentMethod": "OFFLINE"},
                               uuid.uuid4())
        order = Order.objects.get(pk=data["orderId"])
        evidence = VerifiedPayment(order_no=order.order_no, channel="OFFLINE",
                                   merchant_account_id="race-bank", external_trade_no="FAIL-CONFLICT",
                                   amount_fen=order.payable_fen, paid_at=timezone.now(),
                                   source="OFFLINE_RECONCILIATION")
        failure_started, conflict_written = threading.Event(), threading.Event()

        def delayed_failure(receipt_id):
            failure_started.set()
            self.assertTrue(conflict_written.wait(timeout=5))
            return _record_failure(receipt_id)

        def work(index):
            if index == 0:
                return record_verified_payment(evidence)
            self.assertTrue(failure_started.wait(timeout=5))
            try:
                return record_verified_payment(replace(evidence, order_no="O-CONFLICT"))
            finally:
                conflict_written.set()

        with patch("orders.service.consume_order_reservations", side_effect=RuntimeError("test failure")), \
                patch("payments.service._record_failure", side_effect=delayed_failure):
            results, errors = self._race(work)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PaymentError)
        receipt = PaymentReceipt.objects.get()
        self.assertEqual(PaymentAnomaly.objects.get().reason, "IDENTIFIER_CONFLICT")
        self.assertEqual(settle_recorded_payment(receipt.id)["outcome"], "ANOMALY")
        self.assertEqual(Order.objects.get(pk=order.id).status, Order.Status.PENDING_PAYMENT)
