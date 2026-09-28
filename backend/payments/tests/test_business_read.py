import uuid
from datetime import datetime, time, timedelta

from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount, GroupPermission, PermissionGroup
from orders.models import Order
from payments.models import RefundIntent
from payments.tests import test_refund_flow as refund_flow
from payments.refunds import record_verified_refund


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"OFFLINE": True, "WECHAT": True})
class BusinessReadTests(TransactionTestCase):
    _order = refund_flow.RefundFlowTests._order
    _evidence = refund_flow.RefundFlowTests._evidence
    _paid_order = refund_flow.RefundFlowTests._paid_order
    case_and_intent = refund_flow.RefundFlowTests.case_and_intent
    refund_evidence = refund_flow.RefundFlowTests.refund_evidence

    def setUp(self):
        refund_flow.RefundFlowTests.setUp(self)
        self.client = Client()
        self.client.post("/api/v1/admin/auth/login", data='{"loginName":"payment-owner","password":"Long test password 2026!"}',
                         content_type="application/json")

    def test_confirmed_cash_zero_cash_points_and_successful_refund_use_local_days(self):
        order, _, _, intent = self.case_and_intent()
        today = timezone.localdate()
        yesterday = timezone.make_aware(datetime.combine(today - timedelta(days=1), time(23, 30)))
        refund_day = timezone.make_aware(datetime.combine(today, time(0, 30)))
        Order.objects.filter(pk=order.pk).update(paid_at=yesterday)
        self.assertEqual(record_verified_refund(self.refund_evidence(intent))["outcome"], "SUCCEEDED")
        Order.objects.create(order_no=uuid.uuid4().hex, order_kind="CASH", member=self.member,
            quote_id=uuid.uuid4(), status="PAID", payment_method="OFFLINE", goods_total_fen=0,
            shipping_fee_fen=0, payable_fen=0, expires_at=refund_day, paid_at=yesterday)
        result = self.client.get("/api/v1/admin/business-summary", {
            "from": (today - timedelta(days=1)).isoformat(), "to": today.isoformat()})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIn("private", result["Cache-Control"])
        self.assertIn("no-store", result["Cache-Control"])
        data = result.json()["data"]
        self.assertEqual(data["totals"]["paidOrderCount"], 2)
        self.assertEqual(data["totals"]["paidAmountFen"], order.payable_fen)
        self.assertEqual(data["totals"]["pointsExchangeCount"], 0)
        self.assertEqual(data["totals"]["refundCount"], 1)
        self.assertEqual(data["totals"]["refundAmountFen"], intent.amount_fen)
        self.assertEqual(data["days"][0]["paidOrderCount"], 2)
        self.assertEqual(data["days"][0]["refundCount"], 0)
        self.assertEqual(data["days"][1]["refundCount"], 1)
        self.assertLess(data["days"][1]["netAmountFen"], 0)

    def test_separate_permission_and_invalid_range(self):
        group = PermissionGroup.objects.create(code="audit-only", name="仅审计")
        GroupPermission.objects.create(group=group, code="audit.read")
        staff = AdminAccount.objects.create_user("business-staff", "Synthetic password 2026!",
            display_name="员工", kind="STAFF")
        staff.permission_groups.add(group)
        client = Client()
        client.post("/api/v1/admin/auth/login", data='{"loginName":"business-staff","password":"Synthetic password 2026!"}',
                    content_type="application/json")
        self.assertEqual(client.get("/api/v1/admin/business-summary").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/business-summary", {"from": "2020-01-01"}).status_code, 400)

    @override_settings(EXCHANGE_ORDER_ENABLED=True)
    def test_paid_points_exchange_is_separate_from_cash(self):
        from benefits.service import grant_points
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote, submit_exchange_order

        grant_points(self.member, 1000, timezone.now() + timedelta(days=10), "synthetic-report-points")
        offer = ExchangeOffer.objects.create(sku=self.sku, points_price=101, status="ON_SALE")
        quote = create_exchange_quote(self.member, {"offerId": str(offer.id), "quantity": 1})
        submit_exchange_order(self.member, {"quoteId": quote["quoteId"]}, uuid.uuid4())
        result = self.client.get("/api/v1/admin/business-summary")
        self.assertEqual(result.status_code, 200, result.content)
        totals = result.json()["data"]["totals"]
        self.assertEqual(totals["pointsExchangeCount"], 1)
        self.assertEqual(totals["paidOrderCount"], 0)
        self.assertEqual(totals["paidAmountFen"], 0)

    def test_report_read_is_limited_per_account(self):
        for _ in range(30):
            self.assertEqual(self.client.get("/api/v1/admin/business-summary").status_code, 200)
        denied = self.client.get("/api/v1/admin/business-summary")
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(denied.json()["error"]["code"], "RATE_LIMITED")
