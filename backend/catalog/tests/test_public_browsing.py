"""Anonymous category, product and media browsing contracts for the native app."""

import tempfile
import uuid
from pathlib import Path

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from accounts.models import AdminAccount
from catalog.models import (
    Asset, Category, Product, ProductGalleryImage, Sku, SkuSpecSelection, SpecAxis, SpecOption,
)

from .test_media_flow import png


class PublicBrowsingTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.client = Client()
        self.owner = AdminAccount.objects.create_user(
            "public-browse-owner", "Safe owner passphrase 2026!",
            display_name="主账号", kind=AdminAccount.Kind.OWNER,
        )

    def category(self, name, *, parent=None, status="ACTIVE", sort_order=0):
        return Category.objects.create(
            name=name, parent=parent, status=status, sort_order=sort_order,
        )

    def image(self):
        identifier = uuid.uuid4()
        stored_name = f"product/{identifier.hex[:2]}/{identifier}.png"
        path = Path(self.media_dir.name) / stored_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png())
        return Asset.objects.create(
            id=identifier, kind=Asset.Kind.IMAGE, content_type="image/png",
            byte_size=path.stat().st_size, width=1, height=1, sha256="0" * 64,
            original_name="main.png", stored_name=stored_name, created_by=self.owner,
        )

    def product(self, category, number, *, status="ON_SALE", main=None,
                sku_status=None, price=1000):
        sku_status = sku_status or ("ON_SALE" if status == "ON_SALE" else "OFF_SALE")
        if status == "ON_SALE" and sku_status == "OFF_SALE":
            status = "OFF_SALE"
        item = Product.objects.create(
            product_no=number, name=f"商品 {number}", category=category,
            fulfillment_kind=Product.Fulfillment.SHIP, status=status,
            ever_on_sale=status != "DRAFT",
            main_image=main, description_html="<p>详情</p>",
        )
        sku = Sku.objects.create(
            product=item, sku_code=f"SKU-{number}", spec_key="",
            list_price_fen=price, sale_status=sku_status,
        )
        return item, sku

    def test_categories_show_active_tree_in_display_order(self):
        second = self.category("第二类", sort_order=20)
        first = self.category("第一类", sort_order=10)
        hidden = self.category("隐藏类", status="INACTIVE")
        first_child = self.category("子类一", parent=first, sort_order=10)
        second_child = self.category("子类二", parent=second, sort_order=10)
        self.category("隐藏子类", parent=hidden)
        self.category("停用子类", parent=first, status="INACTIVE")

        result = self.client.get("/api/v1/app/categories")
        self.assertEqual(result.status_code, 200, result.content)
        rows = result.json()["data"]
        self.assertEqual([row["id"] for row in rows], [
            str(first.id), str(first_child.id), str(second.id), str(second_child.id),
        ])
        self.assertEqual(rows[1]["parentId"], str(first.id))

    def test_category_filter_pagination_and_stock_unavailable(self):
        root = self.category("酒类")
        leaf = self.category("白酒", parent=root)
        other_leaf = self.category("果酒", parent=root)
        hidden_root = self.category("隐藏", status="INACTIVE")
        hidden_leaf = self.category("隐藏子类", parent=hidden_root)
        image = self.image()
        visible_one, _ = self.product(leaf, "P-ONE", main=image, price=3200)
        visible_two, _ = self.product(leaf, "P-TWO", main=image, price=1200)
        self.product(other_leaf, "P-OTHER", main=image)
        Sku.objects.create(product=visible_two, sku_code="SKU-OFF-CHEAP", spec_key="cheap",
                           list_price_fen=1, sale_status="OFF_SALE")
        self.product(leaf, "P-DRAFT", status="DRAFT", main=image)
        self.product(leaf, "P-OFF", status="OFF_SALE", main=image)
        self.product(leaf, "P-NO-IMAGE")
        self.product(leaf, "P-NO-SKU", main=image, sku_status="OFF_SALE")
        self.product(hidden_leaf, "P-HIDDEN", main=image)

        first_page = self.client.get(f"/api/v1/app/products?categoryId={leaf.id}&page=1&pageSize=1")
        self.assertEqual(first_page.status_code, 200, first_page.content)
        data = first_page.json()["data"]
        self.assertEqual((data["page"], data["pageSize"], data["total"]), (1, 1, 2))
        self.assertEqual(data["rows"][0]["productId"], str(visible_two.id))
        self.assertEqual(data["rows"][0]["minListPriceFen"], 1200)
        self.assertEqual(data["rows"][0]["fulfillmentKind"], "SHIP")
        self.assertEqual(data["rows"][0]["specsPreview"], "默认规格")
        self.assertFalse(data["rows"][0]["purchasable"])
        self.assertEqual(data["rows"][0]["availabilityCode"], "STOCK_NOT_READY")
        self.assertTrue(data["rows"][0]["mainImageUrl"].startswith("/api/v1/app/assets/"))

        second_page = self.client.get(f"/api/v1/app/products?categoryId={root.id}&page=2&pageSize=2")
        self.assertEqual(second_page.status_code, 200, second_page.content)
        self.assertEqual(second_page.json()["data"]["total"], 3)
        self.assertEqual(len(second_page.json()["data"]["rows"]), 1)
        self.assertIn(str(visible_one.id), {
            row["productId"] for row in self.client.get(
                f"/api/v1/app/products?categoryId={leaf.id}"
            ).json()["data"]["rows"]
        })
        with CaptureQueriesContext(connection) as queries:
            self.client.get(f"/api/v1/app/products?categoryId={leaf.id}&pageSize=2")
        # One fixed query reads the administrator payment-method policy.
        self.assertLessEqual(len(queries), 4)

    def test_list_preview_uses_cheapest_sale_sku_ordered_specs_without_n_plus_one(self):
        leaf = self.category("白酒", parent=self.category("酒类"))
        image = self.image()
        item, _ = self.product(leaf, "P-VARIANT", main=image, price=5000)
        item.fulfillment_kind = Product.Fulfillment.REDEEM
        item.save(update_fields=["fulfillment_kind"])
        size = SpecAxis.objects.create(product=item, name="容量", sort_order=1)
        pack = SpecAxis.objects.create(product=item, name="包装", sort_order=2)
        single = SpecOption.objects.create(axis=pack, value="单瓶", sort_order=1)
        bottle = SpecOption.objects.create(axis=size, value="500ml", sort_order=1)
        cheap = Sku.objects.create(product=item, sku_code="SKU-VARIANT-CHEAP",
                                   spec_key="cheap", list_price_fen=1200, sale_status="ON_SALE")
        SkuSpecSelection.objects.create(sku=cheap, axis=pack, option=single)
        SkuSpecSelection.objects.create(sku=cheap, axis=size, option=bottle)
        plain, _ = self.product(leaf, "P-PLAIN", main=image)
        Sku.objects.create(product=item, sku_code="SKU-VARIANT-OFF",
                           spec_key="off", list_price_fen=100, sale_status="OFF_SALE")

        url = f"/api/v1/app/products?categoryId={leaf.id}"
        with CaptureQueriesContext(connection) as one_row_queries:
            self.client.get(f"{url}&pageSize=1")
        with CaptureQueriesContext(connection) as two_row_queries:
            result = self.client.get(f"{url}&pageSize=2")
        self.assertEqual(result.status_code, 200, result.content)
        rows = {row["productId"]: row for row in result.json()["data"]["rows"]}
        self.assertEqual(rows[str(item.id)]["minListPriceFen"], 1200)
        self.assertEqual(rows[str(item.id)]["fulfillmentKind"], "REDEEM")
        self.assertEqual(rows[str(item.id)]["specsPreview"], "容量：500ml · 包装：单瓶")
        self.assertEqual(rows[str(plain.id)]["specsPreview"], "默认规格")
        self.assertEqual(len(two_row_queries), len(one_row_queries))
        # The same singleton policy query is shared by every returned product.
        self.assertLessEqual(len(two_row_queries), 4)

    def test_detail_only_exposes_sale_skus_and_bound_media(self):
        root = self.category("酒类")
        leaf = self.category("白酒", parent=root)
        image = self.image()
        gallery = self.image()
        item, first_sku = self.product(leaf, "P-DETAIL", main=image)
        axis = SpecAxis.objects.create(product=item, name="容量", sort_order=1)
        option = SpecOption.objects.create(axis=axis, value="500ml", sort_order=1)
        SkuSpecSelection.objects.create(sku=first_sku, axis=axis, option=option)
        Sku.objects.create(product=item, sku_code="SKU-OFF", spec_key="off",
                           list_price_fen=100, sale_status="OFF_SALE")
        ProductGalleryImage.objects.create(product=item, asset=gallery, position=0)

        result = self.client.get(f"/api/v1/app/products/{item.id}")
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertEqual([sku["skuCode"] for sku in data["skus"]], ["SKU-P-DETAIL"])
        self.assertEqual(data["skus"][0]["specs"], [{"name": "容量", "value": "500ml"}])
        self.assertEqual(data["galleryImageUrls"], [f"/api/v1/app/assets/{gallery.id}/file"])
        self.assertFalse(data["purchasable"])
        self.assertEqual(data["availabilityCode"], "STOCK_NOT_READY")
        self.assertEqual(data["availabilityMessage"], "库存尚未配置，暂不可购买。")
        self.assertEqual(self.client.get(f"/api/v1/app/assets/{image.id}/file").status_code, 200)

        first_sku.sale_status = "OFF_SALE"
        first_sku.save(update_fields=["sale_status"])
        item.status = "OFF_SALE"
        item.save(update_fields=["status"])
        self.assertEqual(self.client.get(f"/api/v1/app/products/{item.id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/app/assets/{gallery.id}/file").status_code, 404)

    def test_detail_query_count_is_stable_as_skus_are_added(self):
        leaf = self.category("白酒", parent=self.category("酒类"))
        item, _ = self.product(leaf, "P-SKUS", main=self.image())
        path = f"/api/v1/app/products/{item.id}"
        with CaptureQueriesContext(connection) as one_sku_queries:
            self.client.get(path)
        for index in range(2, 8):
            Sku.objects.create(product=item, sku_code=f"SKU-SKUS-{index}",
                               spec_key=str(index), list_price_fen=index * 100,
                               sale_status="ON_SALE")
        with CaptureQueriesContext(connection) as seven_sku_queries:
            result = self.client.get(path)
        self.assertEqual(len(result.json()["data"]["skus"]), 7)
        self.assertEqual(len(seven_sku_queries), len(one_sku_queries))

    def test_public_media_requires_visible_product_and_invalid_page_is_rejected(self):
        root = self.category("酒类")
        leaf = self.category("白酒", parent=root)
        gallery = self.image()
        item, _ = self.product(leaf, "P-NO-MAIN")
        ProductGalleryImage.objects.create(product=item, asset=gallery, position=0)

        self.assertEqual(self.client.get(f"/api/v1/app/assets/{gallery.id}/file").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/app/products/{item.id}").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/app/products?page=0").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/app/products?pageSize=101").status_code, 400)
