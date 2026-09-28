import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from accounts.models import AdminAccount, AuditLog, GroupPermission, PermissionGroup
from catalog.models import Asset, Category, Product, Sku
from catalog.validation import CatalogError
from pages.models import MicroPage, PageConfigVersion, PagePublication, PagePublishRequest


PASSWORD = "Safe owner passphrase 2026!"
DRAFT = "/api/v1/admin/pages/home/draft"
PREVIEW = "/api/v1/admin/pages/home/preview"
PUBLISH = "/api/v1/admin/pages/home/publish"
PUBLIC = "/api/v1/app/home"


class HomeFlowTests(TestCase):
    def test_preview_storage_failure_is_recoverable(self):
        saved = self.save(self.config(asset=self.image()))
        self.assertEqual(saved.status_code, 200, saved.content)
        with patch("catalog.media.available_asset", side_effect=CatalogError(
                "素材存储暂不可用。", "MEDIA_STORAGE_UNAVAILABLE", 503)):
            result = self.send(self.client, "post", PREVIEW, {"expectedRevision": saved.json()["data"]["revision"]})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["code"], "MEDIA_STORAGE_UNAVAILABLE")

    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "page-owner", PASSWORD, display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = self.logged_in(self.owner)
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        setting = override_settings(MEDIA_ROOT=self.media_dir.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def logged_in(self, account):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        self.send(client, "post", "/api/v1/admin/auth/login",
                  {"loginName": account.login_name, "password": PASSWORD})
        return client

    def send(self, client, verb, path, body=None, *, key=None, confirmation=None):
        if path.endswith("/publish") and body is not None and set(body) == {"expectedRevision"}:
            draft = client.get(path[:-len("publish")] + "draft")
            if draft.status_code == 200:
                epochs = getattr(self, "publish_epochs", {})
                identity = (id(client), path, key, body["expectedRevision"])
                epoch = epochs.get(identity, draft.json()["data"]["publicationRevision"])
                self.publish_epochs = {**epochs, identity: epoch}
                body = {**body, "expectedPublicationRevision": epoch}
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        if confirmation:
            headers["HTTP_X_ACTION_CONFIRMATION"] = confirmation
        return getattr(client, verb)(path, data=json.dumps(body or {}),
                                     content_type="application/json", **headers)

    def confirmation(self, page_id, revision):
        result = self.send(self.client, "post", "/api/v1/admin/auth/confirm",
                           {"action": "page.publish", "password": PASSWORD,
                            "objectId": page_id, "revision": revision})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]["confirmationToken"]

    def image(self, created_by=None):
        asset_id = uuid.uuid4()
        path = Path(self.media_dir.name) / f"page/{asset_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-image")
        return Asset.objects.create(
            id=asset_id, kind="IMAGE", content_type="image/png", byte_size=10,
            width=1, height=1, sha256="0" * 64, original_name="banner.png",
            stored_name=f"page/{asset_id}.png", created_by=created_by or self.owner)

    def config(self, *, asset=None, product=None):
        config = self.client.get(DRAFT).json()["data"]["config"]
        components = [{"componentId": "search", "type": "SEARCH", "sortOrder": 10,
                       "visible": True, "props": {"placeholder": "搜索商品"}},
                      {"componentId": "notice", "type": "NOTICE", "sortOrder": 20,
                       "visible": True, "props": {"text": "新品上架",
                                                 "link": {"type": "FUNCTION", "targetId": "CATALOG"}}}]
        if asset:
            slide = {"assetId": str(asset.id)}
            if product:
                slide["link"] = {"type": "PRODUCT", "targetId": str(product.id)}
            components.append({"componentId": "hero", "type": "CAROUSEL", "sortOrder": 30,
                               "visible": True, "props": {"slides": [slide]}})
        return {**config, "components": components}

    def save(self, config, revision=None):
        revision = revision or self.client.get(DRAFT).json()["data"]["revision"]
        return self.send(self.client, "put", DRAFT, {"expectedRevision": revision, "config": config})

    def publish(self, revision, page_id, *, key="publish-key-123"):
        return self.send(self.client, "post", PUBLISH, {"expectedRevision": revision},
                         key=key, confirmation=self.confirmation(page_id, revision))

    def test_permission_draft_isolation_and_publication_idempotency(self):
        self.assertEqual(Client().get(PUBLIC).status_code, 404)
        initial = self.client.get(DRAFT).json()["data"]
        group = PermissionGroup.objects.create(code="page-editor-test", name="页面编辑测试")
        GroupPermission.objects.bulk_create([
            GroupPermission(group=group, code=code) for code in ("page.read", "page.edit", "asset.read")])
        staff = AdminAccount.objects.create_user("page-staff", PASSWORD, display_name="页面编辑")
        staff.permission_groups.add(group)
        staff_client = self.logged_in(staff)
        self.assertEqual(staff_client.get(DRAFT).status_code, 200)
        self.assertEqual(self.send(staff_client, "post", PUBLISH, {}).status_code, 403)
        asset = self.image()
        saved = self.send(staff_client, "put", DRAFT,
                          {"expectedRevision": initial["revision"], "config": self.config(asset=asset)})
        self.assertEqual(saved.status_code, 200, saved.content)
        revision = saved.json()["data"]["revision"]
        self.assertEqual(Client().get(PUBLIC).status_code, 404)
        self.assertEqual(self.send(staff_client, "post", PREVIEW,
                                   {"expectedRevision": revision}).status_code, 200)
        self.assertEqual(self.send(self.client, "post", PUBLISH,
                                   {"expectedRevision": revision}, key="publish-key-123").status_code, 403)
        published = self.publish(revision, initial["pageId"])
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(published.json()["data"]["revision"], revision)
        self.assertEqual(Client().get(PUBLIC).json()["data"]["versionId"],
                         published.json()["data"]["versionId"])
        self.assertEqual(Client().get(f"/api/v1/app/assets/{asset.id}/file").status_code, 200)
        repeat = self.send(self.client, "post", PUBLISH, {"expectedRevision": revision}, key="publish-key-123")
        self.assertEqual(repeat.status_code, 200, repeat.content)
        self.assertEqual(PageConfigVersion.objects.count(), 1)
        self.assertEqual(PagePublishRequest.objects.count(), 1)
        duplicate = self.send(self.client, "post", PUBLISH, {"expectedRevision": revision},
                              key="different-key-456")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["error"]["code"], "PAGE_ALREADY_PUBLISHED")
        self.assertTrue(AuditLog.objects.filter(action_code="page.publish", object_id=initial["pageId"]).exists())

    def test_failed_publish_keeps_previous_version_and_old_asset_is_private(self):
        initial = self.client.get(DRAFT).json()["data"]
        image_one, image_two = self.image(), self.image()
        first = self.save(self.config(asset=image_one)).json()["data"]
        published = self.publish(first["revision"], initial["pageId"]).json()["data"]
        next_config = self.config(asset=image_two)
        next_config["components"][-1]["props"]["slides"][0]["link"] = {
            "type": "PRODUCT", "targetId": str(uuid.uuid4())}
        next_draft = self.save(next_config).json()["data"]
        self.assertEqual(self.send(self.client, "post", PREVIEW,
                                   {"expectedRevision": next_draft["revision"]}).status_code, 422)
        rejected = self.publish(next_draft["revision"], initial["pageId"], key="publish-key-456")
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(Client().get(PUBLIC).json()["data"]["versionId"], published["versionId"])
        self.assertEqual(Client().get(f"/api/v1/app/assets/{image_two.id}/file").status_code, 404)
        next_config["components"][-1]["props"]["slides"][0].pop("link")
        final_draft = self.save(next_config, next_draft["revision"]).json()["data"]
        self.publish(final_draft["revision"], initial["pageId"], key="publish-key-789")
        self.assertEqual(Client().get(f"/api/v1/app/assets/{image_one.id}/file").status_code, 404)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{image_two.id}/file").status_code, 200)

    def test_revision_conflict_key_reuse_and_confirm_binding(self):
        initial = self.client.get(DRAFT).json()["data"]
        config = self.config()
        saved = self.save(config).json()["data"]
        self.assertEqual(self.save(config, initial["revision"]).json()["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.send(self.client, "post", PREVIEW,
                                   {"expectedRevision": initial["revision"]}).status_code, 409)
        wrong_token = self.confirmation(initial["pageId"], initial["revision"])
        self.assertEqual(self.send(self.client, "post", PUBLISH,
                                   {"expectedRevision": saved["revision"]}, key="publish-key-123",
                                   confirmation=wrong_token).status_code, 403)
        self.assertEqual(self.publish(saved["revision"], initial["pageId"]).status_code, 200)
        self.assertEqual(self.send(self.client, "post", PUBLISH,
                                   {"expectedRevision": initial["revision"]},
                                   key="publish-key-123").json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_invalid_assets_links_and_coordinates_rejected(self):
        initial = self.client.get(DRAFT).json()["data"]
        image = self.image()
        config = self.config(asset=image)
        config["components"][-1]["props"]["slides"][0]["assetId"] = str(uuid.uuid4())
        self.assertEqual(self.save(config).status_code, 400)
        config = self.config(asset=image)
        config["components"][-1]["props"]["slides"][0]["link"] = {
            "type": "PAGE", "targetId": str(uuid.uuid4())}
        self.assertEqual(self.save(config).status_code, 400)
        config = self.config(asset=image)
        config["components"].append({"componentId": "hotspot", "type": "IMAGE_HOTZONE",
            "sortOrder": 40, "visible": True, "props": {"assetId": str(image.id), "areas": [
                {"x": .8, "y": 0, "width": .3, "height": .5,
                 "link": {"type": "FUNCTION", "targetId": "SEARCH"}}]}})
        self.assertEqual(self.save(config).status_code, 400)
        self.assertEqual(MicroPage.objects.get().draft_revision, initial["revision"])

    def test_publish_product_link_requires_sale_visibility(self):
        root = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=root)
        image = self.image()
        product = Product.objects.create(product_no="P-HOME", name="商品", category=leaf,
                                         fulfillment_kind="SHIP", status="ON_SALE", ever_on_sale=True,
                                         main_image=image)
        sku = Sku.objects.create(product=product, sku_code="SKU-HOME", spec_key="",
                                 list_price_fen=1000, sale_status="ON_SALE")
        initial = self.client.get(DRAFT).json()["data"]
        draft = self.save(self.config(asset=image, product=product)).json()["data"]
        self.assertEqual(self.publish(draft["revision"], initial["pageId"]).status_code, 200)
        sku.sale_status = "OFF_SALE"
        sku.save(update_fields=["sale_status"])
        product.status = "OFF_SALE"
        product.save(update_fields=["status"])
        later = self.save(self.config(asset=image, product=product), draft["revision"]).json()["data"]
        self.assertEqual(self.publish(later["revision"], initial["pageId"],
                                      key="publish-key-456").status_code, 422)
        self.assertEqual(PageConfigVersion.objects.count(), 1)
        self.assertEqual(PagePublication.objects.get().current_version.revision, draft["revision"])

    def test_hidden_incomplete_components_can_publish_without_leaking_assets(self):
        initial = self.client.get(DRAFT).json()["data"]
        image = self.image()
        config = self.config()
        config["components"].extend([
            {"componentId": "hidden-carousel", "type": "CAROUSEL", "sortOrder": 30,
             "visible": False, "props": {"slides": [{"assetId": str(image.id)}]}},
            {"componentId": "hidden-notice", "type": "NOTICE", "sortOrder": 40,
             "visible": False, "props": {"text": ""}},
            {"componentId": "hidden-filing", "type": "FILING", "sortOrder": 50,
             "visible": False, "props": {"recordNo": ""}},
            {"componentId": "hidden-hotzone", "type": "IMAGE_HOTZONE", "sortOrder": 60,
             "visible": False, "props": {"assetId": "", "areas": []}},
        ])
        saved = self.save(config)
        self.assertEqual(saved.status_code, 200, saved.content)
        revision = saved.json()["data"]["revision"]
        self.assertEqual(self.send(self.client, "post", PREVIEW,
                                   {"expectedRevision": revision}).status_code, 200)
        self.assertEqual(self.publish(revision, initial["pageId"]).status_code, 200)
        public_config = Client().get(PUBLIC).json()["data"]["config"]
        self.assertEqual([item["componentId"] for item in public_config["components"]],
                         ["search", "notice"])
        self.assertEqual(Client().get(f"/api/v1/app/assets/{image.id}/file").status_code, 404)

    def test_unfinished_visible_component_saves_but_cannot_preview_or_publish(self):
        initial = self.client.get(DRAFT).json()["data"]
        config = self.config()
        config["components"].append({"componentId": "pending", "type": "CAROUSEL",
                                      "sortOrder": 30, "visible": True, "props": {"slides": []}})
        saved = self.save(config)
        self.assertEqual(saved.status_code, 200, saved.content)
        revision = saved.json()["data"]["revision"]
        self.assertEqual(self.send(self.client, "post", PREVIEW,
                                   {"expectedRevision": revision}).status_code, 422)
        self.assertEqual(self.publish(revision, initial["pageId"]).status_code, 422)
        self.assertEqual(Client().get(PUBLIC).status_code, 404)

    def test_unfinished_hotzone_area_can_save_but_cannot_publish(self):
        initial = self.client.get(DRAFT).json()["data"]
        config = self.config()
        config["components"].append({"componentId": "pending-hotzone", "type": "IMAGE_HOTZONE",
                                      "sortOrder": 30, "visible": True,
                                      "props": {"assetId": "", "areas": [{"x": 0, "y": 0}]}})
        saved = self.save(config)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(self.publish(saved.json()["data"]["revision"], initial["pageId"]).status_code, 422)

    def test_page_read_only_can_preview_current_home_media_without_asset_read(self):
        group = PermissionGroup.objects.create(code="page-reader-only", name="页面只读测试")
        GroupPermission.objects.create(group=group, code="page.read")
        reader = AdminAccount.objects.create_user("page-reader", PASSWORD, display_name="页面只读")
        reader.permission_groups.add(group)
        reader_client = self.logged_in(reader)
        first, second, unrelated = self.image(), self.image(), self.image()
        asset_url = lambda item: f"/api/v1/admin/assets/{item.id}/file"
        self.assertEqual(reader_client.get(asset_url(first)).status_code, 403)
        self.assertEqual(reader_client.get(asset_url(unrelated)).status_code, 403)
        first_draft = self.save(self.config(asset=first)).json()["data"]
        self.assertEqual(reader_client.get(DRAFT).status_code, 200)
        self.assertEqual(reader_client.get(asset_url(first)).status_code, 200)
        self.assertEqual(reader_client.get(asset_url(unrelated)).status_code, 403)
        self.assertEqual(Client().get(asset_url(first)).status_code, 401)
        self.assertEqual(self.publish(first_draft["revision"], first_draft["pageId"]).status_code, 200)
        second_draft = self.save(self.config(asset=second), first_draft["revision"]).json()["data"]
        self.assertEqual(reader_client.get(asset_url(first)).status_code, 200)
        self.assertEqual(reader_client.get(asset_url(second)).status_code, 200)
        self.assertEqual(self.publish(second_draft["revision"], second_draft["pageId"],
                                      key="second-key-456").status_code, 200)
        self.assertEqual(reader_client.get(asset_url(first)).status_code, 200)
        self.assertEqual(reader_client.get(asset_url(second)).status_code, 200)
        self.assertEqual(self.client.get(asset_url(first)).status_code, 200)

    def test_asset_uploader_can_preview_only_their_unbound_upload(self):
        group = PermissionGroup.objects.create(code="uploader-only", name="素材上传测试")
        GroupPermission.objects.create(group=group, code="asset.upload")
        uploader = AdminAccount.objects.create_user("page-uploader", PASSWORD, display_name="素材上传")
        uploader.permission_groups.add(group)
        uploader_client = self.logged_in(uploader)
        own, other = self.image(created_by=uploader), self.image()
        own_url = f"/api/v1/admin/assets/{own.id}/file"
        other_url = f"/api/v1/admin/assets/{other.id}/file"
        self.assertEqual(uploader_client.get(own_url).status_code, 200)
        self.assertEqual(uploader_client.get(other_url).status_code, 403)
        self.save(self.config(asset=own))
        self.assertEqual(uploader_client.get(own_url).status_code, 403)
