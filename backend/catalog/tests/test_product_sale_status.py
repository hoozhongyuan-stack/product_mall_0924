"""SPU publication is independent of each SKU's chosen sale status."""

import json
from unittest.mock import patch

from django.http import HttpResponse
from django.test import Client, TestCase

from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.models import Asset, Category, Product, Sku, SkuUnitVersion
from catalog.exchange_access import exchange_catalog_rows, eligible_exchange_sku_ids


class ProductSaleStatusTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "product-owner", "A valid owner passphrase 2026!", kind=AdminAccount.Kind.OWNER)
        self.client = Client()
        self.client.get("/api/v1/admin/auth/csrf")
        response = self.client.post("/api/v1/admin/auth/login", json.dumps({
            "loginName": "product-owner", "password": "A valid owner passphrase 2026!",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        parent = Category.objects.create(name="酒类")
        leaf = Category.objects.create(parent=parent, name="白酒")
        image = Asset.objects.create(kind=Asset.Kind.IMAGE, content_type="image/png", byte_size=1,
            width=1, height=1, sha256="0" * 64, original_name="main.png",
            stored_name="product/product-sale-main.png", created_by=self.owner)
        self.product = Product.objects.create(product_no="SPU-SALE", name="商品",
            category=leaf, fulfillment_kind="SHIP", main_image=image)
        self.first = self.sku("SKU-SALE-1", "one")
        self.second = self.sku("SKU-SALE-2", "two")

    def sku(self, code, spec):
        sku = Sku.objects.create(product=self.product, sku_code=code, spec_key=spec,
                                 list_price_fen=1000)
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="件", sale_unit="件", ratio=1)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        return sku

    def sale(self, status, revision, sku_ids=None):
        body = {"saleStatus": status, "expectedRevision": revision}
        if sku_ids is not None:
            body["initialSkuIds"] = [str(item) for item in sku_ids]
        return self.client.patch(f"/api/v1/admin/products/{self.product.id}/sale-status",
                                 json.dumps(body), content_type="application/json")

    def test_draft_requires_explicit_initial_sku_then_manual_off_preserves_selection(self):
        self.assertEqual(self.sale("ON_SALE", 1).status_code, 400)
        self.assertEqual(self.sale("ON_SALE", 1, [self.first.id]).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.ON_SALE)
        self.assertTrue(self.product.ever_on_sale)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.sale_status, Sku.SaleStatus.ON_SALE)
        self.assertEqual(self.second.sale_status, Sku.SaleStatus.OFF_SALE)
        self.assertEqual(self.sale("OFF_SALE", self.product.revision).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.OFF_SALE)
        self.assertTrue(self.product.manually_off_sale)
        self.first.refresh_from_db()
        self.assertEqual(self.first.sale_status, Sku.SaleStatus.ON_SALE)
        self.assertEqual(self.sale("ON_SALE", self.product.revision).status_code, 200)
        self.product.refresh_from_db()
        self.assertFalse(self.product.manually_off_sale)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.sale_status, self.second.sale_status),
                         (Sku.SaleStatus.ON_SALE, Sku.SaleStatus.OFF_SALE))

    def test_batch_is_per_product_atomic_and_reports_revision_conflicts(self):
        other = Product.objects.create(product_no="SPU-OTHER", name="其他", category=self.product.category,
                                       fulfillment_kind="SHIP", main_image=self.product.main_image)
        items = [{"productId": str(self.product.id), "expectedRevision": 1,
                  "initialSkuIds": [str(self.first.id)]},
                 {"productId": str(other.id), "expectedRevision": 999,
                  "initialSkuIds": []}]
        result = self.client.post("/api/v1/admin/products/batch-sale-status", json.dumps({
            "saleStatus": "ON_SALE", "items": items,
        }), content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual((result.json()["data"]["successCount"], result.json()["data"]["failedCount"]), (1, 1))
        self.product.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((self.product.status, other.status), (Product.Status.ON_SALE, Product.Status.DRAFT))

    def test_sku_changes_do_not_republish_manually_hidden_spu(self):
        self.sale("ON_SALE", 1, [self.first.id])
        self.product.refresh_from_db()
        self.sale("OFF_SALE", self.product.revision)
        response = self.client.patch(f"/api/v1/admin/skus/{self.second.id}/status",
            json.dumps({"saleStatus": "ON_SALE", "expectedRevision": self.second.revision}),
            content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.OFF_SALE)

    def test_preview_and_write_both_reject_first_publish_skus_on_off_shelf(self):
        self.sale("ON_SALE", 1, [self.first.id])
        self.product.refresh_from_db()
        payload = {"saleStatus": "OFF_SALE", "items": [{"productId": str(self.product.id),
            "expectedRevision": self.product.revision, "initialSkuIds": [str(self.first.id)]}]}
        preview = self.client.post("/api/v1/admin/products/batch-sale-status/preview",
                                   json.dumps(payload), content_type="application/json")
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertFalse(preview.json()["data"]["items"][0]["canChange"])
        result = self.client.post("/api/v1/admin/products/batch-sale-status",
                                  json.dumps(payload), content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["data"]["failedCount"], 1)

    def test_manual_off_hides_public_and_exchange_without_changing_sku(self):
        self.sale("ON_SALE", 1, [self.first.id])
        self.product.refresh_from_db()
        self.assertEqual(self.sale("OFF_SALE", self.product.revision).status_code, 200)
        listing = self.client.get("/api/v1/app/products")
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertNotIn(str(self.product.id), [row["productId"] for row in listing.json()["data"]["rows"]])
        self.assertEqual(self.client.get(f"/api/v1/app/products/{self.product.id}").status_code, 404)
        self.assertFalse(exchange_catalog_rows([self.first.id])[self.first.id]["eligible"])
        self.assertNotIn(self.first.id, list(eligible_exchange_sku_ids().values_list("id", flat=True)))
        self.first.refresh_from_db()
        self.assertEqual(self.first.sale_status, Sku.SaleStatus.ON_SALE)

    def test_old_spu_revision_cannot_publish_after_hidden_sku_selection_changes(self):
        self.assertEqual(self.sale("ON_SALE", 1, [self.first.id]).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.sale("OFF_SALE", self.product.revision).status_code, 200)
        self.product.refresh_from_db()
        old_revision = self.product.revision
        preview_body = {"saleStatus": "ON_SALE", "items": [
            {"productId": str(self.product.id), "expectedRevision": old_revision}]}
        preview = self.client.post("/api/v1/admin/products/batch-sale-status/preview",
                                   json.dumps(preview_body), content_type="application/json")
        self.assertTrue(preview.json()["data"]["items"][0]["canChange"])
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        for sku, status in ((self.first, "OFF_SALE"), (self.second, "ON_SALE")):
            result = self.client.patch(f"/api/v1/admin/skus/{sku.id}/status",
                json.dumps({"saleStatus": status, "expectedRevision": sku.revision}),
                content_type="application/json")
            self.assertEqual(result.status_code, 200, result.content)
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.OFF_SALE)
        self.assertEqual(self.product.revision, old_revision + 2)
        stale = self.client.post("/api/v1/admin/products/batch-sale-status",
            json.dumps(preview_body), content_type="application/json")
        self.assertEqual(stale.status_code, 200, stale.content)
        self.assertEqual(stale.json()["data"]["results"][0]["code"], "REVISION_CONFLICT")
        self.product.refresh_from_db()
        self.assertTrue(self.product.manually_off_sale)

    def test_all_spu_sale_routes_require_catalog_and_sku_status_permissions(self):
        self.sale("ON_SALE", 1, [self.first.id])
        self.product.refresh_from_db()
        staff = AdminAccount.objects.create_user("sale-catalog-only", "Valid staff password 2026!",
                                                kind=AdminAccount.Kind.STAFF)
        group = PermissionGroup.objects.create(code="sale-catalog-only", name="商品编辑但不能上下架")
        AccountGroup.objects.create(account=staff, group=group)
        GroupPermission.objects.create(group=group, code="catalog.write")
        client = Client()
        client.get("/api/v1/admin/auth/csrf")
        login = client.post("/api/v1/admin/auth/login", json.dumps({
            "loginName": "sale-catalog-only", "password": "Valid staff password 2026!",
        }), content_type="application/json")
        self.assertEqual(login.status_code, 200, login.content)
        batch = {"saleStatus": "OFF_SALE", "items": [{"productId": str(self.product.id),
                 "expectedRevision": self.product.revision}]}
        single = client.patch(f"/api/v1/admin/products/{self.product.id}/sale-status",
            json.dumps({"saleStatus": "OFF_SALE", "expectedRevision": self.product.revision}),
            content_type="application/json")
        preview = client.post("/api/v1/admin/products/batch-sale-status/preview",
                              json.dumps(batch), content_type="application/json")
        execute = client.post("/api/v1/admin/products/batch-sale-status",
                              json.dumps(batch), content_type="application/json")
        self.assertEqual([single.status_code, preview.status_code, execute.status_code], [403, 403, 403])
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.ON_SALE)

    def test_post_lock_permission_recheck_aborts_without_sale_changes(self):
        with patch("catalog.sale_state.require_live", side_effect=[
                (self.owner, None), (None, HttpResponse(status=403))]) as recheck:
            result = self.sale("ON_SALE", 1, [self.first.id])
        self.assertEqual(result.status_code, 403, result.content)
        self.assertEqual(recheck.call_count, 2)
        self.product.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.DRAFT)
        self.assertEqual(self.first.sale_status, Sku.SaleStatus.OFF_SALE)
