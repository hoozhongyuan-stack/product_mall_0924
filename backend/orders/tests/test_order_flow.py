import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount
from benefits.models import CouponCampaign, MemberCoupon, PointsGrant
from benefits.service import grant_points
from catalog.models import Asset, Category, MemberGrade, Product, Sku, SkuUnitVersion
from customers.models import CustomerAddress, Member, MemberSession
from inventory.models import InventoryBalance, Warehouse


@override_settings(WECHAT_MINI_APP_ID="wx-order-test",
                   ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class OrderFlowTests(TestCase):
    def setUp(self):
        from payments.models import OfflinePaymentPolicy
        OfflinePaymentPolicy.objects.update_or_create(pk=1, defaults={
            'instructions': '测试付款说明', 'merchant_account_id': 'test-account'})
        owner = AdminAccount.objects.create_user(
            "order-owner", "Long test password 2026!", display_name="主账号", kind="OWNER")
        self.owner = owner
        image = Asset.objects.create(kind=Asset.Kind.IMAGE, content_type="image/png", byte_size=1,
                                     width=1, height=1, sha256="1" * 64, original_name="test.png",
                                     stored_name="product/test.png", created_by=owner)
        parent = Category.objects.create(name="酒类")
        child = Category.objects.create(name="白酒", parent=parent)
        self.product = Product.objects.create(product_no="ORD-P", name="核销商品", category=child,
                                              fulfillment_kind="REDEEM", status="ON_SALE",
                                              redeem_valid_until=timezone.localdate()+timedelta(days=30),
                                              ever_on_sale=True, main_image=image)
        self.warehouse = Warehouse.objects.create(code="ORDER-W", name="默认仓", is_default=True)
        self.grade = MemberGrade.objects.create(code="order-grade", name="测试会员", rank=99)
        self.member = Member.objects.create(wechat_app_id="wx-order-test", wechat_openid="buyer",
                                            grade=self.grade)
        self.other = Member.objects.create(wechat_app_id="wx-order-test", wechat_openid="other",
                                           grade=self.grade)
        self.token, _ = MemberSession.issue(self.member)
        self.other_token, _ = MemberSession.issue(self.other)
        self.skus = [self._sku("ORD-A", 1000, 10), self._sku("ORD-B", 2000, 10)]

    def _sku(self, code, price, stock):
        sku = Sku.objects.create(product=self.product, sku_code=code, spec_key=code,
                                 list_price_fen=price, sale_status="ON_SALE")
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="件", sale_unit="件", ratio=1)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        InventoryBalance.objects.create(warehouse=self.warehouse, sku=sku,
                                        on_hand_base_units=stock)
        return sku

    def _quote(self, items=None, token=None, **extra):
        body = {"items": items or [{"skuId": str(self.skus[0].id), "quantity": 2}], **extra}
        response = self.client.post("/api/v1/app/checkout/quotes", data=json.dumps(body),
                                    content_type="application/json",
                                    HTTP_AUTHORIZATION=f"Bearer {token or self.token}")
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]["quoteId"]

    def _shipping_address(self):
        self.product.fulfillment_kind = "SHIP"
        self.product.save(update_fields=["fulfillment_kind"])
        return CustomerAddress.objects.create(
            member=self.member, recipient_name="测试收件人", phone="13800000000",
            province="浙江", city="杭州", district="西湖区", detail="测试路 1 号")

    def _cash_coupon(self, discount=300):
        now = timezone.now()
        campaign = CouponCampaign.objects.create(
            code=uuid.uuid4().hex, title="测试现金券", kind="CASH", discount_fen=discount,
            valid_from=now - timedelta(days=1), valid_until=now + timedelta(days=1))
        return MemberCoupon.objects.create(member=self.member, campaign=campaign)

    def test_shipping_coupon_points_snapshot_and_cancel_release(self):
        from orders.models import Order, OrderLine
        from inventory.models import InventoryBalance, InventoryReservation

        address = self._shipping_address()
        coupon = self._cash_coupon()
        grant_points(self.member, 200, timezone.now() + timedelta(days=2), "order-benefit-grant")
        quote_id = self._quote(addressId=str(address.id), couponId=str(coupon.id), pointsToUse=100)
        result = self._submit(quote_id)
        self.assertEqual(result.status_code, 201, result.content)
        data = result.json()["data"]
        self.assertEqual(data["goodsTotalFen"], 2000)
        self.assertEqual(data["shippingFeeFen"], 1000)
        self.assertEqual(data["couponDiscountFen"], 300)
        self.assertEqual(data["pointsDiscountFen"], 100)
        self.assertEqual(data["payableFen"], 2600)
        line = OrderLine.objects.get(order_id=data["orderId"])
        self.assertEqual((line.coupon_discount_fen, line.points_discount_fen, line.payable_fen),
                         (300, 100, 1600))
        self.assertEqual(Order.objects.get(pk=data["orderId"]).coupon_id, coupon.id)
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.id).status, "RESERVED")
        self.assertEqual(PointsGrant.objects.get().reserved_points, 100)
        cancelled = self.client.post(f"/api/v1/app/orders/{data['orderId']}/cancel", data="{}",
                                     content_type="application/json",
                                     HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.id).status, "AVAILABLE")
        self.assertEqual(PointsGrant.objects.get().available_points, 200)
        self.assertEqual(InventoryReservation.objects.get(order_line=line).status, "RELEASED")
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 0)

    def test_shipping_policy_revision_change_rejects_old_quote(self):
        from orders.models import Order
        from shipping.models import ShippingPolicy

        address = self._shipping_address()
        quote_id = self._quote(addressId=str(address.id))
        policy = ShippingPolicy.objects.get(pk=1)
        policy.fee_fen = 1200
        policy.revision += 1
        policy.save(update_fields=["fee_fen", "revision"])
        rejected = self._submit(quote_id)
        self.assertEqual(rejected.status_code, 409, rejected.content)
        self.assertEqual(rejected.json()["error"]["code"], "SHIPPING_CHANGED")
        self.assertFalse(Order.objects.exists())

    def test_mixed_order_charges_shipping_once_and_coupon_only_eligible_product(self):
        from orders.models import OrderLine

        address = self._shipping_address()
        redeem = Product.objects.create(
            product_no="ORD-REDEEM", name="核销商品二", category=self.product.category,
            fulfillment_kind="REDEEM", status="ON_SALE", ever_on_sale=True,
            redeem_valid_until=timezone.localdate()+timedelta(days=30),
            main_image=self.product.main_image)
        sku = Sku.objects.create(product=redeem, sku_code="ORD-REDEEM-SKU", spec_key="only",
                                 list_price_fen=1000, sale_status="ON_SALE")
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="件", sale_unit="件", ratio=1)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        InventoryBalance.objects.create(warehouse=self.warehouse, sku=sku, on_hand_base_units=10)
        coupon = self._cash_coupon()
        coupon.campaign.product_ids = [str(self.product.id)]
        coupon.campaign.save(update_fields=["product_ids"])
        quote_id = self._quote(items=[{"skuId": str(sku.id), "quantity": 1},
                                      {"skuId": str(self.skus[0].id), "quantity": 1}],
                               addressId=str(address.id), couponId=str(coupon.id))
        result = self._submit(quote_id)
        self.assertEqual(result.status_code, 201, result.content)
        data = result.json()["data"]
        self.assertEqual((data["goodsTotalFen"], data["shippingFeeFen"],
                          data["couponDiscountFen"], data["payableFen"]), (2000, 1000, 300, 2700))
        lines = {line.sku_id: line for line in OrderLine.objects.filter(order_id=data["orderId"])}
        self.assertEqual(lines[self.skus[0].id].coupon_discount_fen, 300)
        self.assertEqual(lines[sku.id].coupon_discount_fen, 0)

    def test_coupon_change_and_failure_after_entitlement_hold_roll_back_order(self):
        from orders.models import Order, OrderIdempotency
        from inventory.models import InventoryBalance, InventoryReservation

        coupon = self._cash_coupon(discount=200)
        coupon.campaign.redeem_eligible = True
        coupon.campaign.save(update_fields=["redeem_eligible"])
        grant_points(self.member, 100, timezone.now() + timedelta(days=2), "order-rollback-grant")
        quote_id = self._quote(couponId=str(coupon.id), pointsToUse=100)
        coupon.campaign.active = False
        coupon.campaign.save(update_fields=["active"])
        denied = self._submit(quote_id)
        self.assertEqual(denied.status_code, 409, denied.content)
        self.assertEqual(denied.json()["error"]["code"], "COUPON_UNAVAILABLE")
        self.assertFalse(Order.objects.exists())
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 0)
        coupon.campaign.active = True
        coupon.campaign.save(update_fields=["active"])
        with patch.object(OrderIdempotency.objects, "create", side_effect=DatabaseError("key failed")):
            with self.assertRaises(DatabaseError):
                from orders.service import submit_order
                submit_order(self.member, {"quoteId": quote_id, "paymentMethod": "OFFLINE"}, uuid.uuid4())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(InventoryReservation.objects.exists())
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.id).status, "AVAILABLE")
        self.assertEqual(PointsGrant.objects.get().available_points, 100)
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 0)

    def test_zero_pay_coupon_directly_paid_and_consumes_stock(self):
        from orders.models import Order
        from inventory.models import InventoryBalance, InventoryLedger, InventoryReservation

        coupon = self._cash_coupon(discount=1000)
        coupon.campaign.redeem_eligible = True
        coupon.campaign.save(update_fields=["redeem_eligible"])
        quote_id = self._quote(items=[{"skuId": str(self.skus[0].id), "quantity": 1}],
                               couponId=str(coupon.id))
        result = self._submit(quote_id)
        self.assertEqual(result.status_code, 201, result.content)
        order_id = result.json()["data"]["orderId"]
        self.assertEqual(result.json()["data"]["status"], "PAID")
        self.assertEqual(result.json()["data"]["payableFen"], 0)
        self.assertIsNotNone(Order.objects.get(pk=order_id).paid_at)
        balance = InventoryBalance.objects.get(sku=self.skus[0])
        self.assertEqual((balance.on_hand_base_units, balance.reserved_base_units), (9, 0))
        self.assertEqual(InventoryReservation.objects.get(order_line__order_id=order_id).status, "CONSUMED")
        self.assertEqual(InventoryLedger.objects.get(order_line__order_id=order_id).movement_type, "SALE")
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.id).status, "USED")
        logged_in = self.client.post("/api/v1/admin/auth/login", data=json.dumps({
            "loginName": "order-owner", "password": "Long test password 2026!"}),
            content_type="application/json")
        self.assertEqual(logged_in.status_code, 200, logged_in.content)
        movements = self.client.get("/api/v1/admin/inventory/ledgers?movementType=SALE")
        self.assertEqual(movements.status_code, 200, movements.content)
        self.assertEqual(movements.json()["data"]["total"], 1)
        self.assertEqual(movements.json()["data"]["items"][0]["documentNo"],
                         result.json()["data"]["orderNo"])
        self.assertEqual(movements.json()["data"]["items"][0]["actorName"], "系统")
        self.assertEqual(self.client.post(f"/api/v1/app/orders/{order_id}/cancel", data="{}",
                                          content_type="application/json",
                                          HTTP_AUTHORIZATION=f"Bearer {self.token}").status_code, 409)

    def test_zero_pay_ledger_failure_rolls_back_all_changes(self):
        from orders.models import Order
        from inventory.models import InventoryBalance, InventoryLedger, InventoryReservation

        coupon = self._cash_coupon(discount=1000)
        coupon.campaign.redeem_eligible = True
        coupon.campaign.save(update_fields=["redeem_eligible"])
        quote_id = self._quote(items=[{"skuId": str(self.skus[0].id), "quantity": 1}],
                               couponId=str(coupon.id))
        with patch.object(InventoryLedger.objects, "create", side_effect=DatabaseError("ledger failed")):
            with self.assertRaises(DatabaseError):
                from orders.service import submit_order
                submit_order(self.member, {"quoteId": quote_id, "paymentMethod": "OFFLINE"}, uuid.uuid4())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(InventoryReservation.objects.exists())
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).on_hand_base_units, 10)
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 0)
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.id).status, "AVAILABLE")

    def _submit(self, quote_id, key=None, token=None, **extra):
        return self.client.post("/api/v1/app/orders",
                                data=json.dumps({"quoteId": quote_id, "paymentMethod": "OFFLINE", **extra}),
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
                                HTTP_IDEMPOTENCY_KEY=str(key or uuid.uuid4()))

    def test_submit_snapshots_and_reserves_then_same_key_replays(self):
        from orders.models import Order, OrderLine
        from inventory.models import InventoryReservation

        quote_id = self._quote()
        key = uuid.uuid4()
        first = self._submit(quote_id, key)
        self.assertEqual(first.status_code, 201, first.content)
        order = first.json()["data"]
        self.assertEqual(order["status"], "PENDING_PAYMENT")
        self.assertEqual(order["goodsTotalFen"], 2000)
        self.assertEqual(order["payableFen"], 2000)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderLine.objects.count(), 1)
        self.assertEqual(InventoryReservation.objects.count(), 1)
        balance = InventoryBalance.objects.get(sku=self.skus[0])
        self.assertEqual(balance.reserved_base_units, 2)
        replay = self._submit(quote_id, key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["data"]["orderId"], order["orderId"])
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 2)
        conflict = self._submit(quote_id, key, paymentMethod="WECHAT")
        self.assertEqual(conflict.status_code, 409)

    def test_multi_sku_shortage_rolls_back_everything_and_key_can_retry(self):
        from orders.models import Order, OrderIdempotency
        from inventory.models import InventoryReservation

        quote_id = self._quote([{"skuId": str(s.id), "quantity": 2} for s in self.skus])
        key = uuid.uuid4()
        InventoryBalance.objects.filter(sku=self.skus[1]).update(on_hand_base_units=1)
        failed = self._submit(quote_id, key)
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(failed.json()["error"]["code"], "OUT_OF_STOCK")
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderIdempotency.objects.count(), 0)
        self.assertEqual(InventoryReservation.objects.count(), 0)
        self.assertEqual(sum(InventoryBalance.objects.values_list("reserved_base_units", flat=True)), 0)
        InventoryBalance.objects.filter(sku=self.skus[1]).update(on_hand_base_units=10)
        self.assertEqual(self._submit(quote_id, key).status_code, 201)

    def test_price_change_and_foreign_or_expired_quote_cannot_create_order(self):
        from orders.models import Order

        quote_id = self._quote()
        self.assertEqual(self._submit(quote_id, token=self.other_token).status_code, 404)
        self.skus[0].list_price_fen = 1100
        self.skus[0].save(update_fields=["list_price_fen"])
        changed = self._submit(quote_id)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["error"]["code"], "PRICE_CHANGED")
        self.assertEqual(Order.objects.count(), 0)
        from checkout.models import CheckoutQuote
        CheckoutQuote.objects.filter(pk=quote_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self._submit(quote_id).status_code, 409)

    def test_cancel_and_expiry_release_reservation_once_and_enforce_owner(self):
        from orders.models import Order
        from inventory.models import InventoryReservation
        from orders.service import close_expired_orders

        order = self._submit(self._quote()).json()["data"]
        url = f'/api/v1/app/orders/{order["orderId"]}'
        denied = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.other_token}")
        self.assertEqual(denied.status_code, 404)
        denied_cancel = self.client.post(url + "/cancel", data="{}", content_type="application/json",
                                         HTTP_AUTHORIZATION=f"Bearer {self.other_token}")
        self.assertEqual(denied_cancel.status_code, 404)
        cancelled = self.client.post(url + "/cancel", data="{}", content_type="application/json",
                                     HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["data"]["status"], "CLOSED")
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 0)
        self.assertEqual(InventoryReservation.objects.get().status, "RELEASED")
        self.assertEqual(self.client.post(url + "/cancel", data="{}", content_type="application/json",
                                          HTTP_AUTHORIZATION=f"Bearer {self.token}").status_code, 200)
        second = self._submit(self._quote()).json()["data"]
        with patch("orders.service.timezone.now", return_value=timezone.now() + timedelta(hours=25)):
            self.assertEqual(close_expired_orders(), 1)
            self.assertEqual(close_expired_orders(), 0)
        self.assertEqual(InventoryBalance.objects.get(sku=self.skus[0]).reserved_base_units, 0)

    def test_bad_idempotency_and_unsupported_discount_do_not_reserve(self):
        from orders.models import Order
        quote_id = self._quote()
        self.assertEqual(self._submit(quote_id, pointsToUse=100).status_code, 400)
        self.assertEqual(self._submit(quote_id, couponId=str(uuid.uuid4())).status_code, 400)
        response = self.client.post("/api/v1/app/orders", data=json.dumps({
            "quoteId": quote_id, "paymentMethod": "OFFLINE"}), content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_public_submission_gate_rejects_even_a_valid_quote(self):
        from orders.models import Order
        quote_id = self._quote()
        with override_settings(ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": False, "OFFLINE": False}):
            result = self._submit(quote_id)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["code"], "SETTLEMENT_NOT_READY")
        self.assertEqual(Order.objects.count(), 0)
        anonymous = self.client.post("/api/v1/app/orders", data=json.dumps({
            "quoteId": quote_id, "paymentMethod": "OFFLINE"}), content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(anonymous.status_code, 401)

    def test_payment_method_gate_rejects_only_disabled_method(self):
        from orders.models import Order

        with override_settings(ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": False, "OFFLINE": True}):
            quote = self._quote()
            blocked = self._submit(quote, paymentMethod="WECHAT")
            allowed = self._submit(quote, paymentMethod="OFFLINE")
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(blocked.json()["error"]["code"], "SETTLEMENT_NOT_READY")
        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(Order.objects.count(), 1)

    def test_shipping_quote_without_address_is_rejected(self):
        from orders.models import Order
        self.product.fulfillment_kind = Product.Fulfillment.SHIP
        self.product.save(update_fields=["fulfillment_kind"])
        quote_id = self._quote()
        result = self._submit(quote_id)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["error"]["code"], "ADDRESS_REQUIRED")
        self.assertEqual(Order.objects.count(), 0)

    def test_failure_after_first_reservation_rolls_back_all_rows(self):
        from orders.models import Order, OrderIdempotency
        from inventory.models import InventoryReservation, InventoryReservationEvent

        quote_id = self._quote([{"skuId": str(s.id), "quantity": 2} for s in self.skus])
        key = uuid.uuid4()
        original_create = InventoryReservationEvent.objects.create
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated reservation event failure")
            return original_create(*args, **kwargs)

        with patch.object(InventoryReservationEvent.objects, "create", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self._submit(quote_id, key)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderIdempotency.objects.count(), 0)
        self.assertEqual(InventoryReservation.objects.count(), 0)
        self.assertEqual(InventoryReservationEvent.objects.count(), 0)
        self.assertEqual(sum(InventoryBalance.objects.values_list("reserved_base_units", flat=True)), 0)

    def test_success_key_expires_without_becoming_reusable(self):
        from orders.models import Order, OrderIdempotency
        quote_id = self._quote()
        key = uuid.uuid4()
        first = self._submit(quote_id, key)
        self.assertEqual(first.status_code, 201)
        OrderIdempotency.objects.filter(key=key).update(created_at=timezone.now() - timedelta(hours=25))
        expired = self._submit(quote_id, key)
        self.assertEqual(expired.status_code, 409)
        self.assertEqual(expired.json()["error"]["code"], "IDEMPOTENCY_KEY_EXPIRED")
        self.assertEqual(Order.objects.count(), 1)

    def test_unconfirmed_price_hint_rejected_then_zero_price_directly_paid(self):
        from orders.models import Order
        changed_quote = self._quote([{"skuId": str(self.skus[0].id), "quantity": 1,
                                      "seenPriceFen": 1}])
        changed = self._submit(changed_quote)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["error"]["code"], "PRICE_CONFIRMATION_REQUIRED")
        self.skus[0].list_price_fen = 0
        self.skus[0].save(update_fields=["list_price_fen"])
        zero = self._submit(self._quote())
        self.assertEqual(zero.status_code, 201, zero.content)
        self.assertEqual(zero.json()["data"]["status"], "PAID")
        self.assertEqual(zero.json()["data"]["payableFen"], 0)
        self.assertEqual(Order.objects.count(), 1)

    def test_payment_method_expiry_is_snapshotted_at_creation(self):
        from datetime import datetime
        offline = self._submit(self._quote()).json()["data"]
        wechat = self._submit(self._quote(), paymentMethod="WECHAT").json()["data"]
        offline_window = datetime.fromisoformat(offline["expiresAt"]) - datetime.fromisoformat(offline["createdAt"])
        wechat_window = datetime.fromisoformat(wechat["expiresAt"]) - datetime.fromisoformat(wechat["createdAt"])
        self.assertLess(abs(offline_window - timedelta(hours=24)), timedelta(seconds=2))
        self.assertLess(abs(wechat_window - timedelta(minutes=30)), timedelta(seconds=2))

    def test_order_snapshot_and_reservation_events_are_database_immutable(self):
        from orders.models import Order, OrderLine
        from inventory.models import InventoryReservationEvent

        order_id = self._submit(self._quote()).json()["data"]["orderId"]
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                Order.objects.filter(pk=order_id).update(payable_fen=1)
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                OrderLine.objects.filter(order_id=order_id).update(unit_price_fen=1)
        with transaction.atomic():
            with self.assertRaises(DatabaseError):
                InventoryReservationEvent.objects.filter(reservation__order_line__order_id=order_id).delete()

    def test_redeem_validity_is_order_line_snapshot_and_quote_rechecks_change(self):
        from orders.models import OrderLine
        old_date=self.product.redeem_valid_until
        quote_id=self._quote()
        self.product.redeem_valid_until=old_date+timedelta(days=1)
        self.product.save(update_fields=['redeem_valid_until'])
        changed=self._submit(quote_id)
        self.assertEqual(changed.status_code,409,changed.content)
        self.assertEqual(changed.json()['error']['code'],'REDEEM_VALIDITY_CHANGED')
        fresh=self._quote()
        response=self._submit(fresh)
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(OrderLine.objects.get().redeem_valid_until,self.product.redeem_valid_until)
        self.product.redeem_valid_until=old_date+timedelta(days=2)
        self.product.save(update_fields=['redeem_valid_until'])
        self.assertEqual(OrderLine.objects.get().redeem_valid_until,old_date+timedelta(days=1))
