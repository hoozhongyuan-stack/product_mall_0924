"""Retained references, least-privilege material reads and PostgreSQL races."""
import hashlib
import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch
from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.media import asset_path, orphan_assets, purge_expired_orphans
from catalog.models import Asset, Category, Product, ProductGalleryImage
from catalog.tests.test_media_flow import png
from catalog.page_targets import assets_exist
from pages.models import (MicroPage, PageConfigVersion, PageDraftAsset, PagePublication,
                          PageVersionAsset, StartupConfig, StartupConfigVersion, StartupPublication)
PASSWORD = "Safe material test passphrase 2026!"


def config(asset_id=None):
    return {"schemaVersion": 1, "pageType": "MICRO", "theme": {
        "pageBackgroundColor": "#F7F5F1", "headerBackgroundColor": "#FFFFFF", "brandTextColor": "#25221F"},
        "components": [] if asset_id is None else [{"componentId": "picture", "type": "IMAGE_HOTZONE",
        "sortOrder": 10, "visible": True, "props": {"assetId": str(asset_id), "areas": []}}]}


class AssetFixture:
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        settings = override_settings(MEDIA_ROOT=temporary.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = AdminAccount.objects.create_user("asset-owner", PASSWORD,
            display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = self.login(self.owner)

    def login(self, actor):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = client.post("/api/v1/admin/auth/login", data=json.dumps({
            "loginName": actor.login_name, "password": PASSWORD}), content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def staff(self, codes):
        name = uuid.uuid4().hex[:16]
        group = PermissionGroup.objects.create(code=name, name=name)
        GroupPermission.objects.bulk_create([GroupPermission(group=group, code=code) for code in codes])
        actor = AdminAccount.objects.create_user(name, PASSWORD, display_name=name, kind=AdminAccount.Kind.STAFF)
        AccountGroup.objects.create(account=actor, group=group)
        return actor, group, self.login(actor)

    def asset(self, actor=None):
        data = png()
        asset = Asset.objects.create(kind="IMAGE", content_type="image/png", byte_size=len(data),
            width=1, height=1, sha256=hashlib.sha256(data).hexdigest(), original_name="private.png",
            stored_name=f"product/{uuid.uuid4()}.png", created_by=actor or self.owner)
        path = asset_path(asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return asset

    def page(self):
        page = MicroPage.objects.create(page_type="MICRO", name="受保护页面", draft_config=config())
        PagePublication.objects.create(page=page)
        return page

    def refs(self, asset, client=None):
        result = (client or self.client).get(f"/api/v1/admin/assets/{asset.id}/references")
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]

    def delete(self, asset, client=None):
        client = client or self.client
        return client.delete(f"/api/v1/admin/assets/{asset.id}", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)


class AssetReferenceTests(AssetFixture, TestCase):
    def test_page_draft_current_history_and_hidden_retention_and_public_boundary(self):
        page = self.page()
        draft, history, current, hidden = [self.asset() for _ in range(4)]
        PageDraftAsset.objects.create(page=page, asset=draft)
        old = PageConfigVersion.objects.create(page=page, revision=1, name="历史私密名称",
                                               config_json=config(), published_by=self.owner)
        new = PageConfigVersion.objects.create(page=page, revision=2, name="当前名称",
                                               config_json=config(), published_by=self.owner)
        PageVersionAsset.objects.create(version=old, asset=history, is_public=True)
        PageVersionAsset.objects.create(version=new, asset=current, is_public=True)
        PageVersionAsset.objects.create(version=new, asset=hidden, is_public=False)
        PagePublication.objects.filter(page=page).update(current_version=new, revision=1)
        Asset.objects.filter(id__in=[a.id for a in (draft, history, current, hidden)]).update(
            created_at=timezone.now() - timedelta(days=2))
        _, _, page_reader = self.staff(["page.read"])
        historical_file = page_reader.get(f"/api/v1/admin/assets/{history.id}/file")
        self.assertEqual(historical_file.status_code, 200)
        self.assertEqual(b"".join(historical_file.streaming_content), png())
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(purge_expired_orphans(), 0)
        for asset, state, public in [(draft, "DRAFT", 404), (history, "HISTORY", 404),
                                     (current, "CURRENT", 200), (hidden, "CURRENT", 404)]:
            self.assertFalse(orphan_assets().filter(pk=asset.pk).exists())
            self.assertEqual(self.refs(asset)["items"][0]["state"], state)
            result = self.client.get(f"/api/v1/app/assets/{asset.id}/file")
            self.assertEqual(result.status_code, public)
            if result.status_code == 200:
                self.assertEqual(b"".join(result.streaming_content), png())
            denied = self.delete(asset)
            self.assertEqual(denied.status_code, 409, denied.content)
            self.assertTrue(asset_path(asset).is_file())

    def test_startup_draft_and_history_retained_history_private(self):
        assets = [self.asset() for _ in range(6)]
        draft_gif, draft_image, old_gif, old_image, live_gif, live_image = assets
        startup, _ = StartupConfig.objects.get_or_create(pk=1)
        StartupConfig.objects.filter(pk=startup.pk).update(gif_asset=draft_gif, fallback_asset=draft_image)
        old = StartupConfigVersion.objects.create(revision=1, gif_asset=old_gif,
            fallback_asset=old_image, published_by=self.owner)
        live = StartupConfigVersion.objects.create(revision=2, gif_asset=live_gif,
            fallback_asset=live_image, published_by=self.owner)
        StartupPublication.objects.update_or_create(config=startup, defaults={"current_version": live, "revision": 1})
        Asset.objects.filter(pk__in=[a.pk for a in assets]).update(created_at=timezone.now()-timedelta(days=2))
        self.assertEqual(purge_expired_orphans(), 0)
        for asset, state in zip(assets, ["DRAFT", "DRAFT", "HISTORY", "HISTORY", "CURRENT", "CURRENT"]):
            self.assertEqual(self.refs(asset)["items"][0]["state"], state)
            result = self.client.get(f"/api/v1/app/assets/{asset.id}/file")
            self.assertEqual(result.status_code, 200 if state == "CURRENT" else 404)
            if result.status_code == 200:
                self.assertEqual(b"".join(result.streaming_content), png())
            self.assertEqual(self.delete(asset).status_code, 409)
        self.assertTrue(StartupConfigVersion.objects.filter(pk=old.pk).exists())
        _, _, startup_reader = self.staff(["startup.read"])
        historical_file = startup_reader.get(f"/api/v1/admin/assets/{old_image.id}/file")
        self.assertEqual(historical_file.status_code, 200)
        self.assertEqual(b"".join(historical_file.streaming_content), png())

    def test_upload_permission_lists_only_own_unbound_material(self):
        actor, _, client = self.staff(["asset.upload"])
        own, other, bound = self.asset(actor), self.asset(), self.asset(actor)
        PageDraftAsset.objects.create(page=self.page(), asset=bound)
        result = client.get("/api/v1/admin/assets")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual([row["assetId"] for row in result.json()["data"]["items"]], [str(own.id)])
        for asset in (other, bound):
            self.assertEqual(client.get(f"/api/v1/admin/assets/{asset.id}/file").status_code, 403)
        self.assertEqual(client.get(f"/api/v1/admin/assets/{own.id}/references").status_code, 403)

    def test_reference_metadata_hides_page_identity_without_domain_permission(self):
        _, _, client = self.staff(["asset.read"])
        asset, page = self.asset(), self.page()
        PageDraftAsset.objects.create(page=page, asset=asset)
        result = self.refs(asset, client)
        self.assertEqual(result["total"], 1)
        self.assertIsNone(result["items"][0]["objectId"])
        self.assertEqual(result["items"][0]["label"], "引用受权限保护")
        self.assertNotIn(str(page.id), json.dumps(result))
        self.assertNotIn(page.name, json.dumps(result, ensure_ascii=False))

    def test_catalog_reference_roles_remain_private_without_catalog_read(self):
        _, _, reader = self.staff(["asset.read"])
        parent = Category.objects.create(name="父分类")
        leaf = Category.objects.create(name="子分类", parent=parent)
        main, video, gallery = [self.asset() for _ in range(3)]
        product = Product.objects.create(product_no="PRIVATE-MATERIAL", name="保密商品名称",
            category=leaf, fulfillment_kind="SHIP", main_image=main, video=video)
        ProductGalleryImage.objects.create(product=product, asset=gallery, position=0)
        for asset, role in [(main, "MAIN_IMAGE"), (video, "VIDEO"), (gallery, "GALLERY")]:
            restricted = self.refs(asset, reader)["items"][0]
            self.assertEqual(restricted["role"], role)
            self.assertIsNone(restricted["objectId"])
            self.assertEqual(restricted["label"], "引用受权限保护")
            allowed = self.refs(asset)["items"][0]
            self.assertEqual(allowed["objectId"], str(product.pk))
            self.assertEqual(allowed["label"], product.name)
            self.assertEqual(self.delete(asset).status_code, 409)

    def test_permissions_checked_live_and_revoked_session_rejected(self):
        actor, group, client = self.staff(["asset.read"])
        asset = self.asset()
        self.assertEqual(client.get("/api/v1/admin/assets").status_code, 200)
        GroupPermission.objects.filter(group=group, code="asset.read").delete()
        self.assertEqual(client.get("/api/v1/admin/assets").status_code, 403)
        self.assertEqual(client.get(f"/api/v1/admin/assets/{asset.id}/references").status_code, 403)
        AdminAccount.objects.filter(pk=actor.pk).update(auth_version=actor.auth_version + 1)
        self.assertEqual(client.get("/api/v1/admin/assets").status_code, 401)

    def test_delete_requires_permission_and_csrf(self):
        asset = self.asset()
        _, _, reader = self.staff(["asset.read"])
        self.assertEqual(self.delete(asset, reader).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/admin/assets/{asset.id}").status_code, 403)
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            result = self.delete(asset)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(asset_path(asset).exists())


class AssetReferenceConcurrencyTests(AssetFixture, TransactionTestCase):
    def test_page_binding_blocks_cleanup_until_reference_commits(self):
        page, asset = self.page(), self.asset()
        Asset.objects.filter(pk=asset.pk).update(created_at=timezone.now()-timedelta(days=2))
        bound, release, cleaner_started, cleaner_done = Event(), Event(), Event(), Event()
        pids = []

        def gated_assets(ids):
            result = assets_exist(ids)
            bound.set()
            if not release.wait(10):
                raise RuntimeError("binding gate timed out")
            return result

        def bind():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.append(cursor.fetchone()[0])
                return self.client.put(f"/api/v1/admin/pages/{page.id}/draft", data=json.dumps({
                    "name": page.name, "expectedRevision": 1, "config": config(asset.pk)}),
                    content_type="application/json", HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
            finally:
                connections.close_all()

        def clean():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.append(cursor.fetchone()[0])
                cleaner_started.set()
                return purge_expired_orphans()
            finally:
                cleaner_done.set()
                connections.close_all()

        with override_settings(PRODUCT_MEDIA_ORPHAN_TTL=timedelta(days=3)), \
                patch("pages.views.assets_exist", side_effect=gated_assets), ThreadPoolExecutor(max_workers=2) as pool:
            binding = pool.submit(bind)
            try:
                self.assertTrue(bound.wait(10), "binding did not reach asset validation")
                with override_settings(PRODUCT_MEDIA_ORPHAN_TTL=timedelta(hours=24)):
                    cleanup = pool.submit(clean)
                    self.assertTrue(cleaner_started.wait(10))
                    blocked = not cleaner_done.wait(.3)
                    release.set()
                    response, removed = binding.result(timeout=15), cleanup.result(timeout=15)
            finally:
                release.set()
        self.assertTrue(blocked, "cleanup passed a page binding that had already locked the asset")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(removed, 0)
        self.assertEqual(len(set(pids)), 2)
        self.assertTrue(PageDraftAsset.objects.filter(page=page, asset=asset).exists())
        self.assertTrue(asset_path(asset).is_file())
