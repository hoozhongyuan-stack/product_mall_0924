"""C3 fulfillment operates after trusted payment, without a second stock debit."""
import uuid
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from inventory.models import InventoryLedger
from notifications.models import MessageTask
from orders.models import OrderLine
from payments.service import record_verified_payment
from payments.tests import test_payment_flow as payment_flow
from checkout.service import create_quote
from orders.service import submit_order
from customers.models import CustomerAddress
from customers.models import Member, MemberSession
from accounts.models import AdminAccount


@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class FulfillmentFlowTests(TransactionTestCase):
    _order = payment_flow.PaymentFlowTests._order
    _evidence = payment_flow.PaymentFlowTests._evidence

    def setUp(self):
        payment_flow.PaymentFlowTests.setUp(self)
        self.owner = AdminAccount.objects.get(login_name="payment-owner")

    def _paid_order(self, kind="REDEEM", valid_until=None):
        product = self.sku.product
        if kind == "REDEEM":
            product.fulfillment_kind = "REDEEM"
            product.redeem_valid_until = valid_until or timezone.localdate() + timedelta(days=7)
            product.save(update_fields=["fulfillment_kind", "redeem_valid_until"])
            order = self._order()
        else:
            product.fulfillment_kind = "SHIP"
            product.save(update_fields=["fulfillment_kind"])
            address = CustomerAddress.objects.create(member=self.member, recipient_name="测试收件人",
                phone="13800000000", province="浙江", city="杭州", district="西湖区", detail="测试路1号")
            quote = create_quote({"items": [{"skuId": str(self.sku.id), "quantity": 2}],
                                  "addressId": str(address.id)}, self.member)
            data, _ = submit_order(self.member, {"quoteId": quote["quoteId"],
                                                 "paymentMethod": "OFFLINE"}, uuid.uuid4())
            from orders.models import Order
            order = Order.objects.get(pk=data["orderId"])
        line = OrderLine.objects.get(order=order)
        record_verified_payment(self._evidence(order, trade_no=uuid.uuid4().hex,
                                               event_id=uuid.uuid4().hex))
        order.refresh_from_db()
        return order, line

    def test_member_tracking_is_private_cached_and_correction_invalidates_snapshot(self):
        from fulfillment.models import Carrier, Shipment, ShipmentTrackingSnapshot
        from fulfillment.service import ship_order, correct_shipment

        order, _ = self._paid_order("SHIP")
        Carrier.objects.create(code="SF", name="顺丰速运", enabled=True)
        path = f"/api/v1/app/orders/{order.id}/tracking"
        self.assertEqual(Client().get(path).status_code, 401)
        token, _ = MemberSession.issue(self.member)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        self.assertEqual(Client().get(path, **headers).status_code, 404)
        ship_order(order.id, self.owner, "SF", "SF123456", order.revision, uuid.uuid4())
        with patch("fulfillment.tracking_service.query_kdniao") as provider:
            absent = Client().get(path, **headers)
            self.assertEqual(absent.status_code, 200)
            self.assertEqual(absent.json()["data"]["status"], "UNAVAILABLE")
            self.assertEqual(absent["Cache-Control"], "private, no-store")
            provider.assert_not_called()
        with override_settings(KDNIAO={"ENABLED": True, "EBUSINESS_ID": "test-id", "APP_KEY": "test-key"}):
            with patch("fulfillment.tracking_service.query_kdniao", return_value={
                    "status": "IN_TRANSIT", "events": [{"time": "2026-09-26 12:00:00",
                                                    "description": "已揽收"}]}) as provider:
                first = Client().get(path, **headers).json()["data"]
                second = Client().get(path, **headers).json()["data"]
                self.assertEqual(first["status"], "IN_TRANSIT")
                self.assertEqual(second["events"], first["events"])
                self.assertEqual(provider.call_count, 1)
            shipment = Shipment.objects.get(order=order)
            corrected = correct_shipment(order.id, self.owner, "SF", "SF654321",
                                         "原运单号录入错误", order.revision + 1)
            self.assertEqual(corrected["shipment"]["trackingNo"], "SF654321")
            with patch("fulfillment.tracking_service.query_kdniao", side_effect=Exception("private details")):
                failed = Client().get(path, **headers).json()["data"]
                self.assertEqual(failed["status"], "UNAVAILABLE")
                self.assertEqual(failed["events"], [])
            self.assertEqual(ShipmentTrackingSnapshot.objects.get(shipment=shipment).tracking_no, "SF654321")
            shipment.refresh_from_db()
            self.assertIsNotNone(shipment.shipped_at)
        stranger = Member.objects.create(wechat_app_id=self.member.wechat_app_id,
                                         wechat_openid="tracking-stranger", grade=self.member.grade)
        other_token, _ = MemberSession.issue(stranger)
        self.assertEqual(Client().get(path, HTTP_AUTHORIZATION=f"Bearer {other_token}").status_code, 404)

    def test_ship_once_and_confirm_receipt_do_not_debit_stock_again(self):
        from fulfillment.models import Carrier
        from fulfillment.service import ship_order, confirm_receipt, fulfillment_data

        order, line = self._paid_order("SHIP")
        Carrier.objects.create(code="TEST", name="测试承运商", enabled=True)
        before = InventoryLedger.objects.filter(order_line=line, movement_type="SALE").count()
        shipped = ship_order(order.id, self.owner, "TEST", "TRACK123", order.revision, uuid.uuid4())
        self.assertEqual(MessageTask.objects.filter(event_type="ORDER_SHIPPED", member=self.member).count(), 1)
        self.assertEqual(shipped["fulfillmentStatus"], "IN_PROGRESS")
        self.assertEqual(shipped["shipment"]["warehouseId"], str(line.warehouse_id))
        self.assertEqual(InventoryLedger.objects.filter(order_line=line, movement_type="SALE").count(), before)
        with self.assertRaises(ValueError):
            ship_order(order.id, self.owner, "TEST", "TRACK999", order.revision, uuid.uuid4())
        self.assertEqual(MessageTask.objects.filter(event_type="ORDER_SHIPPED").count(), 1)
        received = confirm_receipt(self.member, order.id)
        self.assertEqual(received["fulfillmentStatus"], "COMPLETED")
        self.assertEqual(fulfillment_data(order, include_code=True)["fulfillmentStatus"], "COMPLETED")

    def test_mixed_order_completes_only_after_shipping_and_all_redemption(self):
        from catalog.models import Product, Sku, SkuUnitVersion
        from fulfillment.models import Carrier, RedeemVoucher
        from fulfillment.service import confirm_receipt, redeem, ship_order
        from inventory.models import InventoryBalance
        from orders.models import Order
        from orders.service import order_data

        address = CustomerAddress.objects.create(member=self.member, recipient_name="混合单收件人",
            phone="13800000000", province="浙江", city="杭州", district="西湖区", detail="测试路 2 号")
        with transaction.atomic():
            physical = Product.objects.create(product_no="PAY-SHIP", name="快递商品",
                category=self.sku.product.category, fulfillment_kind="SHIP", status="ON_SALE",
                ever_on_sale=True, main_image=self.sku.product.main_image)
            shipping_sku = Sku.objects.create(product=physical, sku_code="PAY-SHIP-SKU",
                spec_key="only", list_price_fen=2000, sale_status="ON_SALE")
            unit = SkuUnitVersion.objects.create(sku=shipping_sku, base_unit="件", sale_unit="件", ratio=1)
            shipping_sku.current_unit = unit
            shipping_sku.save(update_fields=["current_unit"])
            InventoryBalance.objects.create(warehouse=self.balance.warehouse, sku=shipping_sku,
                                            on_hand_base_units=5)
        quote = create_quote({"items": [{"skuId": str(self.sku.id), "quantity": 2},
                                        {"skuId": str(shipping_sku.id), "quantity": 1}],
                              "addressId": str(address.id)}, self.member)
        created, _ = submit_order(self.member, {"quoteId": quote["quoteId"],
                                                "paymentMethod": "OFFLINE"}, uuid.uuid4())
        order = Order.objects.get(pk=created["orderId"])
        record_verified_payment(self._evidence(order, trade_no=uuid.uuid4().hex,
                                               event_id=uuid.uuid4().hex))
        order.refresh_from_db()
        self.assertEqual(order_data(order)["fulfillmentStatus"], "WAITING_SHIPMENT")
        Carrier.objects.create(code="MIXED", name="混合单承运商", enabled=True)
        ship_order(order.id, self.owner, "MIXED", "MIXED123", order.revision, uuid.uuid4())
        order.refresh_from_db()
        self.assertEqual(order_data(order)["fulfillmentStatus"], "IN_PROGRESS")
        voucher = RedeemVoucher.objects.get(order_line__order=order)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        self.assertEqual(confirm_receipt(self.member, order.id)["fulfillmentStatus"], "IN_PROGRESS")
        voucher.refresh_from_db()
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        order.refresh_from_db()
        final = order_data(order)
        self.assertEqual(final["fulfillmentStatus"], "COMPLETED")
        self.assertTrue(all(item["fulfillment"]["status"] == "COMPLETED" for item in final["items"]))

    def test_redeem_partial_idempotent_then_reverse_authorized(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem, reverse_redemption, fulfillment_data

        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        self.assertEqual(fulfillment_data(order, include_code=True)["items"][str(line.id)]["remainingQuantity"], 2)
        key = uuid.uuid4()
        first = redeem(voucher.id, self.owner, 1, voucher.revision, key)
        again = redeem(voucher.id, self.owner, 1, voucher.revision, key)
        self.assertEqual(first["eventId"], again["eventId"])
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 1)
        reversed_event = reverse_redemption(first["eventId"], self.owner, "店员误核销", voucher.revision)
        self.assertEqual(reversed_event["remainingQuantity"], 2)
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 0)

    def test_fixed_deadline_allows_redemption_on_its_final_local_day(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem

        order, line = self._paid_order(valid_until=timezone.localdate())
        voucher = RedeemVoucher.objects.get(order_line=line)
        result = redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        self.assertEqual(result["remainingQuantity"], 1)

    def test_invalid_lookup_failures_persist_and_fifth_blocks_further_guesses(self):
        from fulfillment.models import RedeemLookupFailure
        from fulfillment.service import FulfillmentError, lookup_voucher

        self._paid_order()
        for index in range(5):
            with self.assertRaises(FulfillmentError) as raised:
                lookup_voucher("A" * 26, self.owner, f"test-{index}")
            self.assertEqual(raised.exception.code, "VOUCHER_NOT_FOUND")
        self.assertEqual(RedeemLookupFailure.objects.filter(actor=self.owner).count(), 5)
        with self.assertRaises(FulfillmentError) as blocked:
            lookup_voucher("A" * 26, self.owner, "sixth")
        self.assertEqual(blocked.exception.code, "LOOKUP_RATE_LIMITED")

    def test_shipment_correction_audit_and_scheduled_receipt(self):
        from fulfillment.models import Carrier, Shipment, ShipmentCorrection
        from fulfillment.service import ship_order, correct_shipment, auto_confirm_receipts

        order, _ = self._paid_order("SHIP")
        Carrier.objects.create(code="TEST", name="测试承运商", enabled=True)
        Carrier.objects.create(code="TEST2", name="备用承运商", enabled=True)
        ship_order(order.id, self.owner, "TEST", "TRACK123", order.revision, uuid.uuid4())
        order.refresh_from_db()
        corrected = correct_shipment(order.id, self.owner, "TEST2", "TRACK456",
                                     "输入运单号有误", order.revision)
        self.assertEqual(corrected["shipment"]["trackingNo"], "TRACK456")
        audit = ShipmentCorrection.objects.get()
        self.assertEqual((audit.old_tracking_no, audit.new_tracking_no), ("TRACK123", "TRACK456"))
        due = Shipment.objects.get(order=order).auto_confirm_at + timedelta(seconds=1)
        with patch("fulfillment.service.timezone.now", return_value=due):
            self.assertEqual(auto_confirm_receipts(), 1)
            self.assertEqual(auto_confirm_receipts(), 0)
        self.assertEqual(Shipment.objects.get(order=order).confirmed_by_member, False)

    def test_concurrent_distinct_redemptions_of_same_revision_apply_once(self):
        from fulfillment.models import RedeemEvent, RedeemVoucher
        from fulfillment.service import FulfillmentError, redeem

        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        def try_use(_):
            close_old_connections()
            try:
                return redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
            except FulfillmentError as exc:
                return exc.code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(try_use, range(2)))
        self.assertEqual(sum(isinstance(row, dict) for row in results), 1, results)
        self.assertIn("VOUCHER_CHANGED", results)
        voucher.refresh_from_db()
        self.assertEqual(voucher.redeemed_quantity, 1)
        self.assertEqual(RedeemEvent.objects.filter(voucher=voucher, kind="USE").count(), 1)

    def test_database_rejects_over_redeem_and_audit_rewrite(self):
        from fulfillment.models import OrderFulfillmentSnapshot, RedeemEvent, RedeemVoucher
        from fulfillment.service import redeem

        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        with self.assertRaises(DatabaseError), transaction.atomic():
            RedeemVoucher.objects.filter(pk=voucher.id).update(redeemed_quantity=3)
        with self.assertRaises(DatabaseError), transaction.atomic():
            OrderFulfillmentSnapshot.objects.filter(order=order).delete()
        event = redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        with self.assertRaises(DatabaseError), transaction.atomic():
            RedeemEvent.objects.filter(pk=event["eventId"]).update(quantity=2)

    def test_unpaid_closed_and_disabled_carrier_cannot_ship(self):
        from fulfillment.models import Carrier, Shipment
        from fulfillment.service import FulfillmentError, ship_order
        from orders.service import cancel_order

        self.sku.product.redeem_valid_until = timezone.localdate() + timedelta(days=7)
        self.sku.product.save(update_fields=["redeem_valid_until"])
        order = self._order()
        Carrier.objects.create(code="OFF", name="未启用承运商", enabled=False)
        for target in (order,):
            with self.assertRaises(FulfillmentError) as rejected:
                ship_order(target.id, self.owner, "OFF", "TRACK123", target.revision, uuid.uuid4())
            self.assertEqual(rejected.exception.code, "ORDER_NOT_PAID")
        cancel_order(self.member, order.id)
        order.refresh_from_db()
        with self.assertRaises(FulfillmentError) as closed:
            ship_order(order.id, self.owner, "OFF", "TRACK123", order.revision, uuid.uuid4())
        self.assertEqual(closed.exception.code, "ORDER_NOT_PAID")
        self.assertFalse(Shipment.objects.exists())

    def test_batch_ship_is_per_order_and_key_replays_only_its_order(self):
        from fulfillment.models import Carrier, Shipment
        from fulfillment.service import batch_ship

        shipping, _ = self._paid_order("SHIP")
        # A paid redeem-only order must be rejected without affecting shipping.
        self.sku.product.fulfillment_kind = "REDEEM"
        self.sku.product.redeem_valid_until = timezone.localdate() + timedelta(days=7)
        self.sku.product.save(update_fields=["fulfillment_kind", "redeem_valid_until"])
        redemption = self._order()
        record_verified_payment(self._evidence(redemption, trade_no=uuid.uuid4().hex,
                                               event_id=uuid.uuid4().hex))
        redemption.refresh_from_db()
        Carrier.objects.create(code="TEST", name="测试承运商", enabled=True)
        key = str(uuid.uuid4())
        rows = [
            {"orderId": str(shipping.id), "carrierCode": "TEST", "trackingNo": "TRACK123",
             "expectedRevision": shipping.revision, "requestKey": key},
            {"orderId": str(redemption.id), "carrierCode": "TEST", "trackingNo": "TRACK456",
             "expectedRevision": redemption.revision, "requestKey": str(uuid.uuid4())},
        ]
        result = batch_ship(rows, self.owner)
        self.assertEqual((result["succeeded"], result["failed"]), (1, 1))
        self.assertEqual(result["results"][1]["error"]["code"], "NO_SHIPPING_LINES")
        self.assertEqual(batch_ship(rows[:1], self.owner)["succeeded"], 1)
        self.assertEqual(Shipment.objects.count(), 1)

    def test_member_confirmation_ownership_and_admin_code_redaction(self):
        from fulfillment.models import Carrier
        from fulfillment.service import FulfillmentError, confirm_receipt, ship_order
        from orders.queries import admin_order_data

        order, _ = self._paid_order("SHIP")
        Carrier.objects.create(code="TEST", name="测试承运商", enabled=True)
        ship_order(order.id, self.owner, "TEST", "TRACK123", order.revision, uuid.uuid4())
        other = Member.objects.create(wechat_app_id="wx-payment-test", wechat_openid="other-buyer",
                                      grade=self.member.grade)
        with self.assertRaises(FulfillmentError) as denied:
            confirm_receipt(other, order.id)
        self.assertEqual(denied.exception.code, "ORDER_NOT_FOUND")
        self.assertEqual(confirm_receipt(self.member, order.id)["fulfillmentStatus"], "COMPLETED")
        # The admin detail never receives a redeem bearer code, even on paid orders.
        redeem_order, redeem_line = self._paid_order()
        payload = admin_order_data(redeem_order)
        self.assertNotIn("voucherCode", payload["items"][0]["fulfillment"])
        self.assertNotIn("voucherQrDataUrl", payload["items"][0]["fulfillment"])
        from orders.service import order_data
        member_item = order_data(redeem_order, include_voucher_code=True)["items"][0]["fulfillment"]
        self.assertEqual(len(member_item["voucherCode"]), 26)
        self.assertTrue(member_item["voucherQrDataUrl"].startswith("data:image/png;base64,iVBORw0KGgo"))
        token, _ = MemberSession.issue(self.member)
        member_response = Client().get(f"/api/v1/app/orders/{redeem_order.id}",
                                       HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(member_response.status_code, 200)
        self.assertEqual(member_response.json()["data"]["items"][0]["fulfillment"]["voucherCode"],
                         member_item["voucherCode"])
        self.assertEqual(member_response.json()["data"]["items"][0]["fulfillment"]["voucherQrDataUrl"],
                         member_item["voucherQrDataUrl"])
        self.assertEqual(member_response["Cache-Control"], "private, no-store")
        other_token, _ = MemberSession.issue(other)
        self.assertEqual(Client().get(f"/api/v1/app/orders/{redeem_order.id}",
                                      HTTP_AUTHORIZATION=f"Bearer {other_token}").status_code, 404)

    def test_legacy_missing_and_expired_validity_reject_redeem(self):
        from fulfillment.codes import code_digest, new_nonce, voucher_code
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import FulfillmentError, redeem

        order, line = self._paid_order()
        for until, expected in [(None, "VALIDITY_MISSING"),
                                (timezone.localdate() - timedelta(days=1), "VOUCHER_EXPIRED")]:
            legacy = OrderLine.objects.get(pk=line.id)
            legacy.pk = uuid.uuid4()
            duplicate_order = type(order).objects.get(pk=order.id)
            duplicate_order.pk = uuid.uuid4()
            duplicate_order.order_no = f"O{uuid.uuid4().hex.upper()}"
            duplicate_order.save(force_insert=True)
            legacy.order = duplicate_order
            legacy.redeem_valid_until = until
            legacy.save(force_insert=True)
            nonce = new_nonce()
            voucher = RedeemVoucher.objects.create(order_line=legacy, nonce=nonce,
                         code_digest=code_digest(voucher_code(legacy.id, nonce)), valid_until=until)
            with self.assertRaises(FulfillmentError) as rejected:
                redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
            self.assertEqual(rejected.exception.code, expected)

    def test_admin_api_permissions_and_csrf_protect_shipping(self):
        from fulfillment.models import Carrier, Shipment

        order, _ = self._paid_order("SHIP")
        Carrier.objects.create(code="TEST", name="测试承运商", enabled=True)
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        csrf = client.cookies["csrftoken"].value
        login = client.post("/api/v1/admin/auth/login", data=json.dumps({
            "loginName": "payment-owner", "password": "Long test password 2026!"}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(login.status_code, 200, login.content)
        csrf = client.cookies["csrftoken"].value
        path = f"/api/v1/admin/orders/{order.id}/shipment"
        payload = json.dumps({"carrierCode": "TEST", "trackingNo": "TRACK123",
                              "expectedRevision": order.revision})
        self.assertEqual(client.post(path, payload, content_type="application/json",
                                     HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code, 403)
        self.assertFalse(Shipment.objects.exists())
        staff = AdminAccount.objects.create_user("fulfillment-staff", "Long staff password 2026!",
                                                  display_name="无权限店员", kind="STAFF")
        staff_client = Client(enforce_csrf_checks=True)
        staff_client.get("/api/v1/admin/auth/csrf")
        staff_csrf = staff_client.cookies["csrftoken"].value
        self.assertEqual(staff_client.post("/api/v1/admin/auth/login", data=json.dumps({
            "loginName": staff.login_name, "password": "Long staff password 2026!"}),
            content_type="application/json", HTTP_X_CSRFTOKEN=staff_csrf).status_code, 200)
        staff_csrf = staff_client.cookies["csrftoken"].value
        self.assertEqual(staff_client.post(path, payload, content_type="application/json",
            HTTP_X_CSRFTOKEN=staff_csrf, HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code, 403)
        result = client.post(path, payload, content_type="application/json",
                             HTTP_X_CSRFTOKEN=csrf, HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(Shipment.objects.count(), 1)

    def test_admin_and_member_fulfillment_http_flow(self):
        from fulfillment.codes import voucher_code
        from fulfillment.models import RedeemVoucher, Shipment

        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        csrf = client.cookies["csrftoken"].value
        login = client.post("/api/v1/admin/auth/login", data=json.dumps({
            "loginName": "payment-owner", "password": "Long test password 2026!"}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(login.status_code, 200, login.content)
        csrf = client.cookies["csrftoken"].value

        carriers_path = "/api/v1/admin/fulfillment/carriers"
        carrier = client.put(carriers_path, data=json.dumps({"code": "DEMO", "name": "模拟快递",
            "enabled": True, "expectedRevision": 0}), content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(carrier.status_code, 200, carrier.content)
        self.assertEqual(client.get(carriers_path).json()["data"]["items"][0]["code"], "DEMO")
        policy_path = "/api/v1/admin/fulfillment/policy"
        initial = client.get(policy_path)
        self.assertEqual(initial.status_code, 200, initial.content)
        revised = client.put(policy_path, data=json.dumps({"autoConfirmDays": 12,
            "expectedRevision": initial.json()["data"]["revision"]}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(revised.status_code, 200, revised.content)

        shipping, _ = self._paid_order("SHIP")
        ship_path = f"/api/v1/admin/orders/{shipping.id}/shipment"
        shipped = client.post(ship_path, data=json.dumps({"carrierCode": "DEMO",
            "trackingNo": "DEMO-TRACK-1", "expectedRevision": shipping.revision}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(shipped.status_code, 200, shipped.content)
        self.assertEqual(shipped.json()["data"]["shipment"]["trackingNo"], "DEMO-TRACK-1")
        corrected = client.post(f"{ship_path}/correct", data=json.dumps({"carrierCode": "DEMO",
            "trackingNo": "DEMO-TRACK-2", "reason": "原运单号录入错误",
            "expectedRevision": shipped.json()["data"]["revision"]}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(corrected.status_code, 200, corrected.content)
        self.assertEqual(Shipment.objects.get(order=shipping).tracking_no, "DEMO-TRACK-2")
        token, _ = MemberSession.issue(self.member)
        received = Client().post(f"/api/v1/app/orders/{shipping.id}/confirm-receipt",
            data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(received.status_code, 200, received.content)
        self.assertEqual(received.json()["data"]["fulfillmentStatus"], "COMPLETED")

        redemption, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        code = voucher_code(line.id, voucher.nonce)
        lookup = client.post("/api/v1/admin/redemptions/lookup", data=json.dumps({"code": code}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(lookup.status_code, 200, lookup.content)
        self.assertEqual(lookup.json()["data"]["status"], "READY")
        used = client.post(f"/api/v1/admin/redemptions/{voucher.id}/use",
            data=json.dumps({"quantity": 1, "expectedRevision": lookup.json()["data"]["revision"]}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(used.status_code, 200, used.content)
        self.assertEqual(used.json()["data"]["remainingQuantity"], 1)
        reversed_use = client.post(f"/api/v1/admin/redemptions/events/{used.json()['data']['eventId']}/reverse",
            data=json.dumps({"reason": "店员误扫了凭证", "expectedRevision": used.json()["data"]["voucherRevision"]}),
            content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(reversed_use.status_code, 200, reversed_use.content)
        self.assertEqual(reversed_use.json()["data"]["remainingQuantity"], 2)
