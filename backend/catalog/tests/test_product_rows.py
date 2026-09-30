"""The admin product directory is paginated by product, not by sale variant."""

import json

from django.test import Client, TestCase

from accounts.models import AdminAccount
from catalog.models import Category, Product, Sku


class ProductRowsTests(TestCase):
    def setUp(self):
        password = "A valid owner passphrase 2026!"
        AdminAccount.objects.create_user("owner", password, kind=AdminAccount.Kind.OWNER)
        self.client = Client()
        self.client.get("/api/v1/admin/auth/csrf")
        response = self.client.post("/api/v1/admin/auth/login", json.dumps({
            "loginName": "owner", "password": password,
        }), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        parent = Category.objects.create(name="酒类", sort_order=1)
        self.category = Category.objects.create(parent=parent, name="白酒", sort_order=1)

    def product(self, code, prices):
        product = Product.objects.create(product_no=code, name=f"商品 {code}",
                                         category=self.category, fulfillment_kind="SHIP")
        for index, price in enumerate(prices, 1):
            Sku.objects.create(product=product, sku_code=f"{code}-S{index}",
                               spec_key=f"spec-{index}", list_price_fen=price)
        return product

    def rows(self, query=""):
        result = self.client.get(f"/api/v1/admin/product-rows{query}")
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]

    def test_one_row_per_product_and_price_range(self):
        self.product("P1", [2000, 3800, 10800])
        self.product("P2", [5000])
        first = self.rows("?page=1&pageSize=1")
        second = self.rows("?page=2&pageSize=1")
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertNotEqual(first["rows"][0]["productId"], second["rows"][0]["productId"])
        rows = {row["productNo"]: row for row in self.rows()["rows"]}
        self.assertEqual((rows["P1"]["skuCount"], rows["P1"]["minListPriceFen"],
                          rows["P1"]["maxListPriceFen"]), (3, 2000, 10800))
        self.assertEqual(rows["P1"]["onSaleSkuCount"], 0)

    def test_sku_code_search_returns_parent_once(self):
        target = self.product("P1", [2000, 3800])
        self.product("P2", [5000])
        result = self.rows("?keyword=P1-S2")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["productId"], str(target.id))
        self.assertEqual(result["rows"][0]["matchedSkuIds"], [str(target.skus.get(sku_code="P1-S2").id)])

    def test_sku_status_filter_keeps_product_pagination(self):
        target = self.product("P1", [2000, 3800])
        self.product("P2", [5000])
        target.skus.filter(sku_code="P1-S2").update(sale_status=Sku.SaleStatus.ON_SALE)
        Product.objects.filter(pk=target.pk).update(status=Product.Status.ON_SALE, ever_on_sale=True)
        result = self.rows("?skuStatus=ON_SALE")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["productId"], str(target.id))
        self.assertEqual(result["rows"][0]["onSaleSkuCount"], 1)

    def test_invalid_filters_are_rejected(self):
        self.product("P1", [2000])
        for query in ("?productStatus=UNKNOWN", "?skuStatus=UNKNOWN", "?pageSize=101"):
            self.assertEqual(self.client.get(f"/api/v1/admin/product-rows{query}").status_code, 400)

    def test_product_selection_previews_category_for_zero_sku_draft(self):
        draft = Product.objects.create(product_no="EMPTY", name="空规格草稿",
                                       category=self.category, fulfillment_kind="SHIP")
        target = Category.objects.create(parent=self.category.parent, name="新分类", sort_order=2)
        result = self.client.post("/api/v1/admin/products/batch-category/preview", json.dumps({
            "productIds": [str(draft.id)], "categoryId": str(target.id),
        }), content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        item = result.json()["data"]["items"][0]
        self.assertEqual((item["productId"], item["totalSkuCount"]), (str(draft.id), 0))
