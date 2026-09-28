import json
import hashlib
import struct
import tempfile
import uuid
import zlib
from pathlib import Path

from django.db import DatabaseError, transaction
from django.test import Client, TestCase, override_settings

from accounts.models import AdminAccount, GroupPermission, PermissionGroup
from catalog.models import Asset
from pages.models import StorefrontConfig, StorefrontPublication, StorefrontVersion
from pages.storefront_validation import defaults
from .test_startup_flow import static_png


PASSWORD = "Safe owner passphrase 2026!"


class StorefrontTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "storefront-owner", PASSWORD, display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = self.login(self.owner)
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        setting = override_settings(MEDIA_ROOT=media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.media = Path(media.name)

    def login(self, account):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        self.send(client, "post", "/api/v1/admin/auth/login", {
            "loginName": account.login_name, "password": PASSWORD})
        return client

    def send(self, client, verb, path, values, *, key=None, token=None):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        if token:
            headers["HTTP_X_ACTION_CONFIRMATION"] = token
        return getattr(client, verb)(path, data=json.dumps(values), content_type="application/json", **headers)

    def confirm(self, client, domain, operation, object_id, revision):
        result = self.send(client, "post", "/api/v1/admin/auth/confirm", {
            "action": f"{domain}.{operation}", "objectId": object_id,
            "revision": revision, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]["confirmationToken"]

    def put(self, domain, config, revision, *, client=None):
        return self.send(client or self.client, "put", f"/api/v1/admin/{domain}/draft",
                         {"expectedRevision": revision, "config": config})

    def publish(self, domain, revision, epoch, key):
        object_id = domain.replace("-", "_")
        token = self.confirm(self.client, object_id, "publish", object_id, revision)
        return self.send(self.client, "post", f"/api/v1/admin/{domain}/publish",
                         {"expectedRevision": revision, "expectedPublicationRevision": epoch},
                         key=key, token=token)

    def rollback(self, domain, revision, epoch, version_id, key):
        object_id = domain.replace("-", "_")
        token = self.confirm(self.client, object_id, "rollback", f"{object_id}:{version_id}", epoch)
        return self.send(self.client, "post", f"/api/v1/admin/{domain}/rollback", {
            "expectedRevision": revision, "expectedPublicationRevision": epoch,
            "versionId": version_id, "reason": "纠正旧内容"}, key=key, token=token)

    def asset(self):
        identifier = uuid.uuid4()
        path = self.media / f"page/{identifier}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image = static_png()
        path.write_bytes(image)
        return Asset.objects.create(id=identifier, kind=Asset.Kind.IMAGE, content_type="image/png",
                                    byte_size=len(image), width=1, height=1, sha256=hashlib.sha256(image).hexdigest(),
                                    original_name="icon.png", stored_name=f"page/{identifier}.png",
                                    created_by=self.owner)

    def test_independent_publications_history_replay_and_rollback_preserve_drafts(self):
        public = Client().get("/api/v1/app/storefront")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual([item["key"] for item in public.json()["data"]["navigation"]["items"]],
                         ["HOME", "CATEGORY", "CART", "ME"])
        self.assertFalse(public.json()["data"]["customerService"]["enabled"])

        nav_a = self.publish("navigation", 1, 0, "nav-publish-a")
        self.assertEqual(nav_a.status_code, 200, nav_a.content)
        nav_a_id = nav_a.json()["data"]["versionId"]
        service = defaults("customer_service")
        service.update({"enabled": True, "mode": "PHONE", "phone": "13800138000"})
        self.assertEqual(self.put("customer-service", service, 1).status_code, 200)
        svc_a = self.publish("customer-service", 2, 0, "svc-publish-a")
        self.assertEqual(svc_a.status_code, 200, svc_a.content)
        svc_a_id = svc_a.json()["data"]["versionId"]
        nav = defaults("navigation")
        icon, selected = self.asset(), self.asset()
        nav["items"][0]["iconAssetId"] = str(icon.id)
        nav["items"][0]["selectedIconAssetId"] = str(selected.id)
        self.assertEqual(self.put("navigation", nav, 1).status_code, 200)
        nav_b = self.publish("navigation", 2, 1, "nav-publish-b")
        self.assertEqual(nav_b.status_code, 200, nav_b.content)
        nav_b_id = nav_b.json()["data"]["versionId"]
        self.assertEqual(self.client.get(f"/api/v1/admin/navigation/versions").json()["data"]["total"], 2)
        self.assertEqual(self.client.get(f"/api/v1/admin/navigation/versions/{nav_a_id}").status_code, 200)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{icon.id}/file").status_code, 200)
        self.assertEqual(self.put("navigation", nav, 2).status_code, 200)
        rolled = self.rollback("navigation", 3, 2, nav_a_id, "nav-rollback-a")
        self.assertEqual(rolled.status_code, 200, rolled.content)
        self.assertEqual(rolled.json()["data"]["draftRevision"], 3)
        self.assertEqual(self.client.get("/api/v1/admin/navigation/draft").json()["data"]["revision"], 3)
        self.assertEqual(StorefrontPublication.objects.get(config_id="customer_service").current_version_id.hex,
                         uuid.UUID(svc_a_id).hex)
        self.assertEqual(Client().get("/api/v1/app/storefront").json()["data"]["customerService"]["phone"],
                         "13800138000")
        self.assertEqual(Client().get(f"/api/v1/app/assets/{icon.id}/file").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/admin/assets/{icon.id}/file").status_code, 200)
        references = self.client.get(f"/api/v1/admin/assets/{icon.id}/references")
        self.assertEqual(references.status_code, 200, references.content)
        self.assertIn("HISTORY", [row["state"] for row in references.json()["data"]["items"]])
        from catalog.media import orphan_assets
        self.assertFalse(orphan_assets().filter(id=icon.id).exists())
        replay = self.send(self.client, "post", "/api/v1/admin/navigation/rollback", {
            "expectedRevision": 3, "expectedPublicationRevision": 2,
            "versionId": nav_a_id, "reason": "纠正旧内容"}, key="nav-rollback-a")
        self.assertEqual(replay.json()["data"], rolled.json()["data"])
        conflicting = self.send(self.client, "post", "/api/v1/admin/navigation/rollback", {
            "expectedRevision": 3, "expectedPublicationRevision": 2,
            "versionId": nav_a_id, "reason": "改用其他原因"}, key="nav-rollback-a")
        self.assertEqual(conflicting.status_code, 409)
        self.assertEqual(conflicting.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(StorefrontPublication.objects.get(config_id="navigation").revision, 3)
        restored = self.rollback("navigation", 3, 3, nav_b_id, "nav-restore-b")
        self.assertEqual(restored.status_code, 200, restored.content)
        old_publish = self.send(self.client, "post", "/api/v1/admin/navigation/publish", {
            "expectedRevision": 1, "expectedPublicationRevision": 0}, key="nav-publish-a")
        self.assertEqual(old_publish.json()["data"], nav_a.json()["data"])
        self.assertEqual(StorefrontPublication.objects.get(config_id="navigation").current_version_id,
                         uuid.UUID(nav_b_id))
        self.assertNotEqual(nav_b_id, nav_a_id)

    def test_invalid_target_and_permissions_preserve_current(self):
        nav = self.publish("navigation", 1, 0, "nav-first-publish")
        self.assertEqual(nav.status_code, 200, nav.content)
        first = nav.json()["data"]["versionId"]
        icon, selected = self.asset(), self.asset()
        config = defaults("navigation")
        config["items"][0].update({"iconAssetId": str(icon.id), "selectedIconAssetId": str(selected.id)})
        self.assertEqual(self.put("navigation", config, 1).status_code, 200)
        second = self.publish("navigation", 2, 1, "nav-second-publish")
        self.assertEqual(second.status_code, 200, second.content)
        (self.media / icon.stored_name).unlink()
        failed = self.rollback("navigation", 2, 2, second.json()["data"]["versionId"], "nav-invalid-target")
        self.assertEqual(failed.status_code, 409)  # already-current checked before media
        # Reverting the valid old version succeeds; restoring the missing-file version fails.
        reverted = self.rollback("navigation", 2, 2, first, "nav-revert-first")
        self.assertEqual(reverted.status_code, 200, reverted.content)
        missing = self.rollback("navigation", 2, 3, second.json()["data"]["versionId"], "nav-missing-file")
        self.assertEqual(missing.status_code, 422, missing.content)
        self.assertEqual(StorefrontPublication.objects.get(config_id="navigation").current_version_id,
                         uuid.UUID(first))
        group = PermissionGroup.objects.create(code="navigation-reader-e11", name="导航只读")
        GroupPermission.objects.create(group=group, code="navigation.read")
        reader = AdminAccount.objects.create_user("nav-reader", PASSWORD, display_name="只读")
        reader.permission_groups.add(group)
        client = self.login(reader)
        self.assertEqual(client.get("/api/v1/admin/navigation/versions").status_code, 200)
        self.assertEqual(client.get("/api/v1/admin/customer-service/versions").status_code, 403)
        self.assertEqual(self.put("navigation", defaults("navigation"), 2, client=client).status_code, 403)
        self.assertEqual(self.send(client, "post", "/api/v1/admin/navigation/rollback", {
            "expectedRevision": 2, "expectedPublicationRevision": 3,
            "versionId": first, "reason": "无权"}, key="nav-reader-rollback").status_code, 403)

    def test_rejects_route_or_label_changes_and_cross_domain_version(self):
        nav = defaults("navigation")
        nav["items"][0]["label"] = "活动"
        self.assertEqual(self.put("navigation", nav, 1).status_code, 400)
        nav = defaults("navigation")
        nav["items"][0]["route"] = "/hidden"
        self.assertEqual(self.put("navigation", nav, 1).status_code, 400)
        service = defaults("customer_service")
        service.update({"enabled": True, "mode": "PHONE", "phone": "13800138000", "qrAssetId": str(uuid.uuid4())})
        self.assertEqual(self.put("customer-service", service, 1).status_code, 422)
        nav_pub = self.publish("navigation", 1, 0, "nav-domain-pub")
        self.assertEqual(nav_pub.status_code, 200, nav_pub.content)
        other = self.client.get(f"/api/v1/admin/customer-service/versions/{nav_pub.json()['data']['versionId']}")
        self.assertEqual(other.status_code, 404)

    def test_default_content_group_can_edit_but_cannot_publish(self):
        group = PermissionGroup.objects.get(code="member_marketing")
        member = AdminAccount.objects.create_user("storefront-editor", PASSWORD, display_name="内容编辑")
        member.permission_groups.add(group)
        client = self.login(member)
        self.assertEqual(client.get("/api/v1/admin/navigation/draft").status_code, 200)
        self.assertEqual(client.get("/api/v1/admin/customer-service/versions").status_code, 200)
        self.assertEqual(self.put("navigation", defaults("navigation"), 1, client=client).status_code, 200)
        self.assertEqual(self.send(client, "post", "/api/v1/admin/navigation/publish", {
            "expectedRevision": 2, "expectedPublicationRevision": 0}, key="editor-cannot-publish").status_code, 403)

    def test_database_guards_immutable_version_receipts_and_domain_pointer(self):
        first = self.publish("navigation", 1, 0, "nav-db-first")
        self.assertEqual(first.status_code, 200, first.content)
        version_id = first.json()["data"]["versionId"]
        with self.assertRaises(DatabaseError), transaction.atomic():
            StorefrontVersion.objects.filter(pk=version_id).update(revision=9)
        service = StorefrontConfig.objects.get(pk="customer_service")
        with self.assertRaises(DatabaseError), transaction.atomic():
            StorefrontPublication.objects.filter(config=service).update(current_version_id=version_id, revision=1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            StorefrontPublication.objects.filter(config_id="navigation").update(revision=2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            StorefrontPublication.objects.filter(config_id="navigation").update(current_version_id=None, revision=2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            StorefrontPublication.objects.filter(config_id="navigation").delete()
        self.assertEqual(StorefrontPublication.objects.get(config_id="navigation").revision, 1)

    def test_reader_can_read_history_image_without_asset_read(self):
        icon, selected = self.asset(), self.asset()
        nav = defaults("navigation")
        nav["items"][0].update({"iconAssetId": str(icon.id), "selectedIconAssetId": str(selected.id)})
        self.assertEqual(self.put("navigation", nav, 1).status_code, 200)
        first = self.publish("navigation", 2, 0, "nav-image-first")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(self.put("navigation", defaults("navigation"), 2).status_code, 200)
        second = self.publish("navigation", 3, 1, "nav-image-second")
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{icon.id}/file").status_code, 404)
        group = PermissionGroup.objects.create(code="nav-history-reader-e11", name="导航历史只读")
        GroupPermission.objects.create(group=group, code="navigation.read")
        reader = AdminAccount.objects.create_user("nav-history-reader", PASSWORD, display_name="只读")
        reader.permission_groups.add(group)
        client = self.login(reader)
        self.assertEqual(client.get(f"/api/v1/admin/assets/{icon.id}/file").status_code, 200)
        self.assertEqual(client.get(f"/api/v1/admin/navigation/versions/{first.json()['data']['versionId']}").status_code, 200)
        self.assertEqual(client.get(f"/api/v1/admin/customer-service/versions/{first.json()['data']['versionId']}").status_code, 403)

    def test_qr_service_public_only_while_enabled_and_image_must_be_valid(self):
        qr, icon = self.asset(), self.asset()
        service = defaults("customer_service")
        service.update({"enabled": True, "mode": "QR", "qrAssetId": str(qr.id),
                        "prompt": "扫码咨询", "iconAssetId": str(icon.id)})
        self.assertEqual(self.put("customer-service", service, 1).status_code, 200)
        preview = self.send(self.client, "post", "/api/v1/admin/customer-service/preview",
                            {"expectedRevision": 2})
        self.assertEqual(preview.status_code, 200, preview.content)
        first = self.publish("customer-service", 2, 0, "service-qr-first")
        self.assertEqual(first.status_code, 200, first.content)
        public = Client().get("/api/v1/app/storefront").json()["data"]["customerService"]
        self.assertEqual(public["mode"], "QR")
        self.assertEqual(public["prompt"], "扫码咨询")
        self.assertEqual(public["qrUrl"], f"/api/v1/app/assets/{qr.id}/file")
        self.assertEqual(Client().get(public["qrUrl"]).status_code, 200)

        disabled = {**service, "enabled": False}
        self.assertEqual(self.put("customer-service", disabled, 2).status_code, 200)
        second = self.publish("customer-service", 3, 1, "service-disabled-second")
        self.assertEqual(second.status_code, 200, second.content)
        public = Client().get("/api/v1/app/storefront").json()["data"]["customerService"]
        self.assertFalse(public["enabled"])
        self.assertIsNone(public["qrUrl"])
        self.assertEqual(Client().get(f"/api/v1/app/assets/{qr.id}/file").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/admin/assets/{qr.id}/file").status_code, 200)
        self.assertEqual(self.rollback("customer-service", 3, 2,
                                      first.json()["data"]["versionId"], "service-qr-rollback").status_code, 200)

        (self.media / qr.stored_name).write_bytes(b"corrupt")
        self.assertEqual(self.rollback("customer-service", 3, 3,
                                      second.json()["data"]["versionId"], "service-disabled-rollback").status_code,
                         422)
        self.assertEqual(StorefrontPublication.objects.get(config_id="customer_service").revision, 3)
        self.assertEqual(StorefrontPublication.objects.get(config_id="customer_service").current_version_id,
                         uuid.UUID(first.json()["data"]["versionId"]))

    def test_rejects_unicode_phone_digits_and_same_size_image_replacement(self):
        service = defaults("customer_service")
        service.update({"enabled": True, "mode": "PHONE", "phone": "13８８８８８８８８８"})
        invalid_phone = self.put("customer-service", service, 1)
        self.assertEqual(invalid_phone.status_code, 422, invalid_phone.content)

        icon, selected = self.asset(), self.asset()
        nav = defaults("navigation")
        nav["items"][0].update({"iconAssetId": str(icon.id), "selectedIconAssetId": str(selected.id)})
        self.assertEqual(self.put("navigation", nav, 1).status_code, 200)
        published = self.publish("navigation", 2, 0, "nav-image-integrity")
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(self.put("navigation", defaults("navigation"), 2).status_code, 200)
        current = self.publish("navigation", 3, 1, "nav-default-current")
        self.assertEqual(current.status_code, 200, current.content)

        def chunk(kind, payload):
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
        alternate = (b"\x89PNG\r\n\x1a\n" +
                     chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(b"\x00\x00\xff\x00")) + chunk(b"IEND", b""))
        self.assertEqual(len(alternate), icon.byte_size)
        from catalog.media import png_dimensions
        self.assertEqual(png_dimensions(alternate), (1, 1))
        (self.media / icon.stored_name).write_bytes(alternate)
        rejected = self.rollback("navigation", 3, 2, published.json()["data"]["versionId"],
                                 "nav-tampered-image")
        self.assertEqual(rejected.status_code, 422, rejected.content)
        pointer = StorefrontPublication.objects.get(config_id="navigation")
        self.assertEqual(pointer.current_version_id, uuid.UUID(current.json()["data"]["versionId"]))
        self.assertEqual(pointer.revision, 2)
