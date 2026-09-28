"""C4.1 synthetic PostgreSQL drill across jobs, money and order read models."""
import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount
from catalog.models import Product, Sku, SkuUnitVersion
from checkout.service import create_quote
from customers.models import CustomerAddress, MemberSession
from fulfillment.models import Carrier, RedeemVoucher, Shipment
from fulfillment.service import redeem, ship_order
from inventory.models import InventoryBalance, InventoryLedger, InventoryReservation
from orders.models import Order
from orders.service import order_data, submit_order
from payments.models import OfflinePaymentPolicy, PaymentAnomaly, PaymentReceipt
from payments.service import record_verified_payment
from payments.tests import test_payment_flow as payment_flow
from payments.wechat_config import WechatGatewayError


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class PhaseCRehearsalTests(TransactionTestCase):
    _order = payment_flow.PaymentFlowTests._order
    _evidence = payment_flow.PaymentFlowTests._evidence

    def setUp(self):
        payment_flow.PaymentFlowTests.setUp(self)
        self.owner = AdminAccount.objects.get(login_name="payment-owner")
        self.address = CustomerAddress.objects.create(member=self.member,
            recipient_name="合成收件人", phone="13800000000", province="浙江", city="杭州",
            district="西湖区", detail="演练路1号")
        with transaction.atomic():
            product = Product.objects.create(product_no="C4-SHIP", name="合成快递商品",
                category=self.sku.product.category, fulfillment_kind="SHIP", status="ON_SALE",
                ever_on_sale=True, main_image=self.sku.product.main_image)
            self.ship_sku = Sku.objects.create(product=product, sku_code="C4-SHIP-SKU",
                spec_key="only", list_price_fen=2000, sale_status="ON_SALE")
            unit = SkuUnitVersion.objects.create(sku=self.ship_sku, base_unit="件", sale_unit="件", ratio=1)
            self.ship_sku.current_unit = unit
            self.ship_sku.save(update_fields=["current_unit"])
            InventoryBalance.objects.create(warehouse=self.balance.warehouse, sku=self.ship_sku,
                                            on_hand_base_units=10)
        Carrier.objects.create(code="C4TEST", name="合成承运商", enabled=True)

    def _create_shipping(self, *, mixed=False):
        lines = [{"skuId": str(self.ship_sku.id), "quantity": 1}]
        if mixed:
            lines.append({"skuId": str(self.sku.id), "quantity": 2})
        quote = create_quote({"items": lines, "addressId": str(self.address.id)}, self.member)
        created, _ = submit_order(self.member, {"quoteId": quote["quoteId"],
                                                "paymentMethod": "OFFLINE"}, uuid.uuid4())
        return Order.objects.get(pk=created["orderId"])

    def _pay_and_ship(self, order, tracking):
        record_verified_payment(self._evidence(order, trade_no=f"C4-{tracking}",
                                               event_id=f"C4-EVENT-{tracking}"))
        order.refresh_from_db()
        ship_order(order.id, self.owner, "C4TEST", tracking, order.revision, uuid.uuid4())
        order.refresh_from_db()

    @patch("orders.management.commands.run_phase_c_jobs.load_wechat_config",
           side_effect=WechatGatewayError("unconfigured"))
    def test_due_jobs_retry_late_funds_and_mixed_progress(self, _wechat_config):
        now = timezone.now()
        policy = OfflinePaymentPolicy.objects.get(pk=1)
        policy.offline_timeout_minutes = 1
        policy.save(update_fields=["offline_timeout_minutes"])
        with patch("django.utils.timezone.now", return_value=now - timedelta(minutes=2)):
            expired = self._order()
        with patch("django.utils.timezone.now", return_value=now - timedelta(days=11)):
            due = self._create_shipping()
            self._pay_and_ship(due, "C4DUE123")
        mixed = self._create_shipping(mixed=True)
        self._pay_and_ship(mixed, "C4MIXED123")
        voucher = RedeemVoucher.objects.get(order_line__order=mixed)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        before_sale = InventoryLedger.objects.filter(movement_type="SALE").count()

        first = StringIO()
        with patch("orders.management.commands.run_phase_c_jobs.auto_confirm_receipts",
                   side_effect=RuntimeError("synthetic receipt job failure")):
            with self.assertRaises(CommandError):
                call_command("run_phase_c_jobs", limit=100, stdout=first, stderr=StringIO())
        self.assertIn("closed=1", first.getvalue())
        self.assertIn("auto_confirmed=FAILED", first.getvalue())
        self.assertEqual(Order.objects.get(pk=expired.id).status, Order.Status.CLOSED)
        self.assertIsNone(Shipment.objects.get(order=due).confirmed_at)

        second = StringIO()
        call_command("run_phase_c_jobs", limit=100, stdout=second, stderr=StringIO())
        self.assertIn("closed=0", second.getvalue())
        self.assertIn("auto_confirmed=1", second.getvalue())
        self.assertIn("wechat=SKIPPED_UNCONFIGURED", second.getvalue())
        third = StringIO()
        call_command("run_phase_c_jobs", limit=100, stdout=third, stderr=StringIO())
        self.assertIn("closed=0 auto_confirmed=0", third.getvalue())
        self.assertEqual(_wechat_config.call_count, 3)
        self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), before_sale)
        self.assertEqual(InventoryReservation.objects.filter(order_line__order=expired,
                                                              status="RELEASED").count(), 1)
        self.assertEqual(order_data(Order.objects.get(pk=due.id))["fulfillmentStatus"], "COMPLETED")
        self.assertEqual(order_data(Order.objects.get(pk=mixed.id))["fulfillmentStatus"], "IN_PROGRESS")

        late = self._evidence(expired, trade_no="C4-LATE-TRADE", event_id="C4-LATE-EVENT")
        self.assertEqual(record_verified_payment(late)["outcome"], "ANOMALY")
        self.assertEqual(record_verified_payment(late)["outcome"], "ANOMALY")
        self.assertEqual(PaymentReceipt.objects.filter(order=expired).count(), 1)
        self.assertEqual(PaymentAnomaly.objects.get(receipt__order=expired).reason, "CLOSED_ORDER")
        self.assertEqual(Order.objects.get(pk=expired.id).status, Order.Status.CLOSED)

        token, _ = MemberSession.issue(self.member)
        member_client = Client()
        detail = member_client.get(f"/api/v1/app/orders/{due.id}",
                                   HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["data"]["fulfillmentStatus"], "COMPLETED")
        mixed_detail = member_client.get(f"/api/v1/app/orders/{mixed.id}",
                                         HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(mixed_detail.json()["data"]["fulfillmentStatus"], "IN_PROGRESS")
