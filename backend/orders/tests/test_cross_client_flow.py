"""C4.2: member and operator HTTP contracts share one durable order state."""
import json
import uuid

from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from customers.models import MemberSession
from fulfillment.models import RedeemVoucher
from inventory.models import InventoryLedger
from orders.models import Order
from orders.tests import test_phase_c_rehearsal as rehearsal
from payments.models import OfflinePaymentPolicy, OfflinePaymentReport, PaymentReceipt


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"OFFLINE": True, "WECHAT": False})
class CrossClientFlowTests(TransactionTestCase):
    def setUp(self):
        rehearsal.PhaseCRehearsalTests.setUp(self)
        policy = OfflinePaymentPolicy.objects.get(pk=1)
        policy.instructions = "合成测试付款说明，不得真实转账"
        policy.merchant_account_id = "C42-SYNTHETIC-BANK"
        policy.save(update_fields=["instructions", "merchant_account_id"])
        token, _ = MemberSession.issue(self.member)
        self.member_client = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
        login = self._post(self.client, "/api/v1/admin/auth/login", {
            "loginName": "payment-owner", "password": "Long test password 2026!"})
        self.assertEqual(login.status_code, 200)

    def _post(self, client, path, body, key=None, **headers):
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return client.post(path, data=json.dumps(body), content_type="application/json", **headers)

    def test_report_requires_authorized_funds_before_fulfillment(self):
        quote = self._post(self.member_client, "/api/v1/app/checkout/quotes", {
            "items": [{"skuId": str(self.ship_sku.id), "quantity": 1},
                      {"skuId": str(self.sku.id), "quantity": 2}],
            "addressId": str(self.address.id)})
        self.assertEqual(quote.status_code, 201, quote.content)
        self.assertEqual(quote.json()["data"]["availablePaymentMethods"], ["OFFLINE"])
        submission = {"quoteId": quote.json()["data"]["quoteId"], "paymentMethod": "OFFLINE"}
        submit_key = str(uuid.uuid4())
        order = self._post(self.member_client, "/api/v1/app/orders", submission, submit_key)
        self.assertEqual(order.status_code, 201, order.content)
        order_id = order.json()["data"]["orderId"]
        replay = self._post(self.member_client, "/api/v1/app/orders", submission, submit_key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["data"]["orderId"], order_id)

        report_path = f"/api/v1/app/orders/{order_id}/payment-report"
        report_key = str(uuid.uuid4())
        report = self._post(self.member_client, report_path, {"note": "合成报告"}, report_key)
        self.assertEqual(report.status_code, 201, report.content)
        self.assertEqual(report.json()["data"]["paymentReviewStatus"], "PENDING_REVIEW")
        self.assertEqual(self._post(self.member_client, report_path,
                                    {"note": "合成报告"}, report_key).status_code, 200)
        self.assertEqual(Order.objects.get(pk=order_id).status, "PENDING_PAYMENT")
        self.assertEqual(PaymentReceipt.objects.count(), 0)
        self.assertEqual(OfflinePaymentReport.objects.count(), 1)

        admin = self.client.get(f"/api/v1/admin/orders/{order_id}").json()["data"]
        self.assertEqual(admin["paymentReviewStatus"], "PENDING_REVIEW")
        early_ship = self._post(self.client, f"/api/v1/admin/orders/{order_id}/shipment", {
            "carrierCode": "C4TEST", "trackingNo": "C42EARLY", "expectedRevision": admin["revision"]},
            str(uuid.uuid4()))
        self.assertEqual(early_ship.status_code, 409)

        prepare = self._post(self.client, f"/api/v1/admin/orders/{order_id}/offline-reconciliations", {
            "expectedRevision": admin["revision"], "merchantAccountId": "C42-SYNTHETIC-BANK",
            "externalTradeNo": "C42-TRADE-ONE", "amountFen": admin["payableFen"],
            "paidAt": timezone.now().isoformat(), "note": "合成资金凭证", "verified": True},
            str(uuid.uuid4()))
        self.assertEqual(prepare.status_code, 201, prepare.content)
        intent = prepare.json()["data"]
        confirm_path = f"/api/v1/admin/payments/offline-reconciliations/{intent['reconciliationId']}/confirm"
        self.assertEqual(self._post(self.client, confirm_path, {}).status_code, 403)
        authorize = self._post(self.client, "/api/v1/admin/auth/confirm", {
            "action": "payment.offline.confirm", "objectId": intent["reconciliationId"],
            "revision": 1, "password": "Long test password 2026!"})
        self.assertEqual(authorize.status_code, 200, authorize.content)
        confirmation = authorize.json()["data"]["confirmationToken"]
        confirmed = self._post(self.client, confirm_path, {},
                               HTTP_X_ACTION_CONFIRMATION=confirmation)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["data"]["outcome"], "PAID")
        self.assertEqual(self._post(self.client, confirm_path, {}).json()["data"]["outcome"], "PAID")
        self.assertEqual(PaymentReceipt.objects.count(), 1)
        sale_count = InventoryLedger.objects.filter(movement_type="SALE").count()
        self.assertEqual(sale_count, 2)

        paid = self.member_client.get(f"/api/v1/app/orders/{order_id}").json()["data"]
        self.assertEqual(paid["status"], "PAID")
        self.assertEqual(paid["fulfillmentStatus"], "WAITING_SHIPMENT")
        shipped = self._post(self.client, f"/api/v1/admin/orders/{order_id}/shipment", {
            "carrierCode": "C4TEST", "trackingNo": "C42TRACK123", "expectedRevision": paid["revision"]},
            str(uuid.uuid4()))
        self.assertEqual(shipped.status_code, 200, shipped.content)
        voucher = RedeemVoucher.objects.get(order_line__order_id=order_id)
        used = self._post(self.client, f"/api/v1/admin/redemptions/{voucher.id}/use", {
            "quantity": 1, "expectedRevision": voucher.revision}, str(uuid.uuid4()))
        self.assertEqual(used.status_code, 200, used.content)
        self.assertEqual(used.json()["data"]["remainingQuantity"], 1)
        detail = self.member_client.get(f"/api/v1/app/orders/{order_id}").json()["data"]
        self.assertEqual(detail["fulfillmentStatus"], "IN_PROGRESS")
        self.assertEqual(detail["shipment"]["trackingNo"], "C42TRACK123")
        self.assertEqual(InventoryLedger.objects.filter(movement_type="SALE").count(), sale_count)

    def test_cancelled_order_rejects_report_and_wechat_submission(self):
        quote = self._post(self.member_client, "/api/v1/app/checkout/quotes", {
            "items": [{"skuId": str(self.sku.id), "quantity": 1}]})
        self.assertEqual(quote.status_code, 201, quote.content)
        quote_id = quote.json()["data"]["quoteId"]
        blocked = self._post(self.member_client, "/api/v1/app/orders", {
            "quoteId": quote_id, "paymentMethod": "WECHAT"}, str(uuid.uuid4()))
        self.assertEqual(blocked.status_code, 503)
        made = self._post(self.member_client, "/api/v1/app/orders", {
            "quoteId": quote_id, "paymentMethod": "OFFLINE"}, str(uuid.uuid4()))
        self.assertEqual(made.status_code, 201, made.content)
        order_id = made.json()["data"]["orderId"]
        cancel_path = f"/api/v1/app/orders/{order_id}/cancel"
        self.assertEqual(self._post(self.member_client, cancel_path, {}).status_code, 200)
        self.assertEqual(self._post(self.member_client, cancel_path, {}).status_code, 200)
        report = self._post(self.member_client, f"/api/v1/app/orders/{order_id}/payment-report",
                            {"note": "关单后报告"}, str(uuid.uuid4()))
        self.assertEqual(report.status_code, 409)
        self.assertEqual(Order.objects.get(pk=order_id).status, "CLOSED")
