import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount
from catalog.models import Asset, Category, MemberGrade, Product, Sku, SkuGradePrice, SkuUnitVersion
from inventory.models import InventoryBalance, StockPool, StockPoolSku, Warehouse
from customers.models import CustomerAddress, Member, MemberSession


class QuoteApiTests(TestCase):
    def setUp(self):
        parent = Category.objects.create(name="茶酒")
        child = Category.objects.create(name="白酒", parent=parent)
        owner = AdminAccount.objects.create_user("quote-owner", "Long test password 2026!",
                                                  display_name="主账号", kind=AdminAccount.Kind.OWNER)
        image = Asset.objects.create(kind=Asset.Kind.IMAGE, content_type="image/png", byte_size=1,
                                     width=1, height=1, sha256="0" * 64, original_name="quote.png",
                                     stored_name="product/quote.png", created_by=owner)
        self.product = Product.objects.create(product_no="P1", name="测试酒", category=child,
                                              fulfillment_kind=Product.Fulfillment.REDEEM,
                                              redeem_valid_until=timezone.localdate()+timedelta(days=30),
                                              status=Product.Status.ON_SALE, ever_on_sale=True, main_image=image)
        self.sku = Sku.objects.create(product=self.product, sku_code="S1", spec_key="default",
                                      list_price_fen=1200, sale_status=Sku.SaleStatus.ON_SALE)
        unit = SkuUnitVersion.objects.create(sku=self.sku, base_unit="瓶", sale_unit="箱", ratio=6)
        self.sku.current_unit = unit
        self.sku.save(update_fields=["current_unit"])
        warehouse = Warehouse.objects.create(code="W1", name="主仓", is_default=True)
        self.balance = InventoryBalance.objects.create(warehouse=warehouse, sku=self.sku,
                                                       on_hand_base_units=18, reserved_base_units=0)

    def quote(self, items=None, **extra):
        body = {"items": items if items is not None else [{"skuId": str(self.sku.id), "quantity": 2}], **extra}
        return self.client.post("/api/v1/app/checkout/quotes", data=json.dumps(body),
                                content_type="application/json")

    def test_available_guest_quote_uses_current_stock_and_daily_price(self):
        result = self.quote().json()["data"]
        self.assertEqual(result["goodsTotalFen"], 2400)
        self.assertEqual(result["lines"][0]["availableQuantity"], 3)
        self.assertEqual(result["lines"][0]["baseQuantity"], 12)
        self.assertTrue(result["ready"])
        self.assertFalse(result["orderSubmissionAvailable"])
        self.assertEqual(result["availablePaymentMethods"], [])

    def test_shared_pool_mixed_skus_cannot_quote_more_than_physical_stock(self):
        single = Sku.objects.create(product=self.product, sku_code="S-SINGLE", spec_key="single",
                                    list_price_fen=300, sale_status=Sku.SaleStatus.ON_SALE)
        unit = SkuUnitVersion.objects.create(sku=single, base_unit="瓶", sale_unit="瓶", ratio=1)
        single.current_unit = unit
        single.save(update_fields=["current_unit"])
        pool = StockPool.objects.create(anchor_sku=self.sku, base_unit="瓶")
        StockPoolSku.objects.create(sku=self.sku, pool=pool)
        StockPoolSku.objects.create(sku=single, pool=pool)
        result = self.quote(items=[{"skuId": str(self.sku.id), "quantity": 2},
                                   {"skuId": str(single.id), "quantity": 7}]).json()["data"]
        self.assertFalse(result["ready"])
        self.assertEqual([line["availableQuantity"] for line in result["lines"]], [3, 18])
        self.assertIn("OUT_OF_STOCK", [line["status"] for line in result["lines"]])

    def test_quote_exposes_only_enabled_payment_method(self):
        with override_settings(ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": False, "OFFLINE": True}):
            result = self.quote().json()["data"]
        self.assertEqual(result["availablePaymentMethods"], ["OFFLINE"])
        self.assertTrue(result["orderSubmissionAvailable"])

    def test_shipping_quote_uses_configured_fee_once(self):
        self.product.fulfillment_kind = Product.Fulfillment.SHIP
        self.product.save(update_fields=["fulfillment_kind"])
        result = self.quote().json()["data"]
        self.assertEqual(result["goodsTotalFen"], 2400)
        self.assertEqual(result["shippingFeeFen"], 1000)
        self.assertEqual(result["payableFen"], 3400)
        self.assertFalse(result["shippingFeePending"])
        self.assertTrue(result["addressRequired"])
        self.assertEqual(result["couponDiscountFen"], 0)
        self.assertEqual(result["pointsDiscountFen"], 0)

    def test_redeem_quote_has_zero_shipping_fee_and_no_discount(self):
        result = self.quote().json()["data"]
        self.assertEqual(result["shippingFeeFen"], 0)
        self.assertEqual(result["couponDiscountFen"], 0)
        self.assertEqual(result["pointsDiscountFen"], 0)
        self.assertEqual(result["payableFen"], 2400)

    def test_missing_shipping_configuration_fails_closed(self):
        from shipping.models import ShippingPolicy

        ShippingPolicy.objects.all().delete()
        rejected = self.quote()
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(rejected.json()["error"]["code"], "SHIPPING_UNAVAILABLE")

    def test_unavailable_or_changed_price_requires_attention(self):
        result = self.quote(items=[{"skuId": str(self.sku.id), "quantity": 4,
                                                "seenPriceFen": 1000}]).json()["data"]
        self.assertFalse(result["ready"])
        self.assertTrue(result["confirmRequired"])
        self.assertEqual(result["lines"][0]["status"], "OUT_OF_STOCK")

    def test_rejects_duplicate_sku_and_unsupported_discount(self):
        row = {"skuId": str(self.sku.id), "quantity": 1}
        self.assertEqual(self.quote(items=[row, row]).status_code, 400)
        self.assertEqual(self.quote(couponId="anything").status_code, 400)
        self.assertEqual(self.quote(pointsToUse=1).status_code, 400)

    def test_disabled_default_warehouse_cannot_quote_ready(self):
        Warehouse.objects.filter(is_default=True).update(enabled=False)
        result = self.quote().json()["data"]
        self.assertFalse(result["ready"])
        self.assertEqual(result["lines"][0]["status"], "WAREHOUSE_UNAVAILABLE")

    def test_missing_sku_is_explicit_unavailable_not_a_partial_order(self):
        result = self.quote(items=[{"skuId": "11111111-1111-1111-1111-111111111111", "quantity": 1}])
        self.assertEqual(result.status_code, 201)
        self.assertFalse(result.json()["data"]["ready"])
        self.assertEqual(result.json()["data"]["lines"][0]["status"], "NOT_FOUND")

    def test_delisted_product_does_not_leak_unpublished_details(self):
        self.sku.sale_status = Sku.SaleStatus.OFF_SALE
        self.sku.save(update_fields=["sale_status"])
        self.product.status = Product.Status.OFF_SALE
        self.product.save(update_fields=["status"])
        result = self.quote().json()["data"]
        self.assertFalse(result["ready"])
        self.assertEqual(result["lines"][0]["name"], "商品已失效")
        self.assertIsNone(result["lines"][0]["imageUrl"])
        self.assertEqual(result["lines"][0]["status"], "OFF_SALE")

    @override_settings(WECHAT_MINI_APP_ID="wx-test")
    def test_member_grade_price_and_invalid_token_rejection(self):
        grade = MemberGrade.objects.create(code="testgrade", name="测试会员", rank=88)
        member = Member.objects.create(wechat_app_id="wx-test", wechat_openid="quote-member", grade=grade)
        SkuGradePrice.objects.create(sku=self.sku, grade=grade, price_fen=900)
        token, _ = MemberSession.issue(member)
        result = self.client.post("/api/v1/app/checkout/quotes",
                                  data=json.dumps({"items": [{"skuId": str(self.sku.id), "quantity": 2}]}),
                                  content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()["data"]["goodsTotalFen"], 1800)
        self.assertEqual(result.json()["data"]["lines"][0]["priceSource"], "GRADE")
        denied = self.client.post("/api/v1/app/checkout/quotes", data='{"items":[]}',
                                  content_type="application/json", HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(denied.status_code, 401)

    @override_settings(WECHAT_MINI_APP_ID="wx-test")
    def test_member_cannot_quote_with_another_members_address(self):
        grade = MemberGrade.objects.create(code="testgrade", name="测试会员", rank=88)
        member = Member.objects.create(wechat_app_id="wx-test", wechat_openid="owner", grade=grade)
        other = Member.objects.create(wechat_app_id="wx-test", wechat_openid="other", grade=grade)
        address = CustomerAddress.objects.create(member=other, recipient_name="李四", phone="13800000000",
                                                 province="上海", city="上海", district="徐汇",
                                                 detail="测试路 1 号", is_default=True)
        token, _ = MemberSession.issue(member)
        result = self.client.post("/api/v1/app/checkout/quotes",
                                  data=json.dumps({"items": [{"skuId": str(self.sku.id), "quantity": 1}],
                                                   "addressId": str(address.id)}),
                                  content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json()["error"]["code"], "VALIDATION_FAILED")

    def test_redeem_quote_snapshots_visible_validity_and_rejects_missing(self):
        from datetime import timedelta
        from django.utils import timezone
        date=timezone.localdate()+timedelta(days=10)
        self.product.redeem_valid_until=date
        self.product.save(update_fields=['redeem_valid_until'])
        result=self.quote().json()['data']
        self.assertEqual(result['lines'][0]['redeemValidUntil'],date.isoformat())
        self.assertTrue(result['ready'])
        self.product.redeem_valid_until=None
        self.product.save(update_fields=['redeem_valid_until'])
        result=self.quote().json()['data']
        self.assertFalse(result['ready'])
