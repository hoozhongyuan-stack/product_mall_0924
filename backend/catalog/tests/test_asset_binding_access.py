"""Private material binding authority is enforced by all draft write APIs."""
import json
import uuid

from django.test import TestCase

from catalog.models import Category, Product
from catalog.tests.test_asset_references import AssetFixture, config
from pages.models import PageDraftAsset, StartupConfig


class AssetBindingAccessTests(AssetFixture, TestCase):
    EDIT_CODES = ("catalog.write", "page.edit", "startup.edit", "sku.status.write",
                  "sku.price.write", "sku.unit.write")

    def target(self, domain):
        if domain == "catalog":
            parent = Category.objects.create(name="父分类")
            leaf = Category.objects.create(name="子分类", parent=parent)
            return Product.objects.create(product_no=uuid.uuid4().hex, name="素材权限商品",
                category=leaf, fulfillment_kind="SHIP")
        if domain == "page":
            return self.page()
        return StartupConfig.objects.get_or_create(pk=1)[0]

    def bind(self, domain, target, asset, client):
        target.refresh_from_db()
        if domain == "catalog":
            path, verb = f"/api/v1/admin/products/{target.pk}", "patch"
            body = {"expectedRevision": target.revision, "mainImageAssetId": str(asset.pk)}
        elif domain == "page":
            path, verb = f"/api/v1/admin/pages/{target.pk}/draft", "put"
            body = {"expectedRevision": target.draft_revision, "name": target.name,
                    "config": config(asset.pk)}
        else:
            path, verb = "/api/v1/admin/startup/draft", "put"
            body = {"expectedRevision": target.draft_revision, "gifAssetId": None,
                    "fallbackAssetId": str(asset.pk)}
        return getattr(client, verb)(path, data=json.dumps(body), content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    def state(self, domain, target):
        target.refresh_from_db()
        if domain == "catalog":
            return (target.revision, target.main_image_id)
        if domain == "page":
            return (target.draft_revision, target.draft_config,
                    list(PageDraftAsset.objects.filter(page=target).values_list("asset_id", flat=True)))
        return (target.draft_revision, target.gif_asset_id, target.fallback_asset_id)

    def denied_unchanged(self, domain, target, asset, client):
        before = self.state(domain, target)
        result = self.bind(domain, target, asset, client)
        self.assertEqual(result.status_code, 403, result.content)
        self.assertEqual(result.json()["error"]["code"], "PERMISSION_DENIED")
        self.assertEqual(self.state(domain, target), before)

    def test_editors_without_material_permissions_cannot_bind_foreign_uuid(self):
        _, _, editor = self.staff(self.EDIT_CODES)
        for domain in ("catalog", "page", "startup"):
            with self.subTest(domain=domain):
                self.denied_unchanged(domain, self.target(domain), self.asset(), editor)

    def test_upload_authority_allows_only_own_unbound_material(self):
        actor, _, editor = self.staff((*self.EDIT_CODES, "asset.upload"))
        for domain in ("catalog", "page", "startup"):
            with self.subTest(domain=domain):
                target = self.target(domain)
                self.denied_unchanged(domain, target, self.asset(), editor)
                bound = self.asset(actor)
                PageDraftAsset.objects.create(page=self.page(), asset=bound)
                self.denied_unchanged(domain, target, bound, editor)
                own = self.asset(actor)
                allowed = self.bind(domain, target, own, editor)
                self.assertEqual(allowed.status_code, 200, allowed.content)

    def test_existing_material_on_same_object_can_be_retained_without_library_authority(self):
        _, _, editor = self.staff(self.EDIT_CODES)
        for domain in ("catalog", "page", "startup"):
            with self.subTest(domain=domain):
                target, asset = self.target(domain), self.asset()
                self.assertEqual(self.bind(domain, target, asset, self.client).status_code, 200)
                retained = self.bind(domain, target, asset, editor)
                self.assertEqual(retained.status_code, 200, retained.content)
                self.denied_unchanged(domain, target, self.asset(), editor)

    def test_owner_can_reuse_material_bound_to_another_object(self):
        asset = self.asset()
        PageDraftAsset.objects.create(page=self.page(), asset=asset)
        for domain in ("catalog", "page", "startup"):
            with self.subTest(domain=domain):
                result = self.bind(domain, self.target(domain), asset, self.client)
                self.assertEqual(result.status_code, 200, result.content)

    def test_product_creation_cannot_bypass_material_binding_authority(self):
        _, _, editor = self.staff(self.EDIT_CODES)
        parent = Category.objects.create(name="父分类")
        leaf = Category.objects.create(name="子分类", parent=parent)
        body = {"productNo": "PRIVATE-BINDING-CREATE", "name": "私有素材", "categoryId": str(leaf.pk),
                "fulfillmentKind": "SHIP", "descriptionHtml": "", "specAxes": [],
                "mainImageAssetId": str(self.asset().pk), "skus": [{
                    "skuCode": "PRIVATE-BINDING-SKU", "specOptionKeys": [], "listPriceFen": 100,
                    "saleStatus": "OFF_SALE", "gradePrices": [],
                    "unit": {"baseUnit": "瓶", "saleUnit": "瓶", "ratio": 1}}]}
        result = editor.post("/api/v1/admin/products", data=json.dumps(body),
            content_type="application/json", HTTP_X_CSRFTOKEN=editor.cookies["csrftoken"].value)
        self.assertEqual(result.status_code, 403, result.content)
        self.assertEqual(result.json()["error"]["code"], "PERMISSION_DENIED")
        self.assertFalse(Product.objects.filter(product_no=body["productNo"]).exists())
