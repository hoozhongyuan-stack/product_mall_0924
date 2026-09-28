import json
import subprocess
import struct
import tempfile
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import AdminAccount, AuditLog, GroupPermission, PermissionGroup
from catalog.models import Asset
from catalog.validation import CatalogError
from pages.models import StartupConfig, StartupConfigVersion, StartupPublishRequest


PASSWORD = "Safe owner passphrase 2026!"
DRAFT = "/api/v1/admin/startup/draft"
PREVIEW = "/api/v1/admin/startup/preview"
PUBLISH = "/api/v1/admin/startup/publish"
PUBLIC = "/api/v1/app/startup"


def static_png():
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload +
                struct.pack(">I", zlib.crc32(kind + payload)))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
            chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


class StartupFlowTests(TestCase):
    def test_preview_storage_failure_is_recoverable(self):
        saved = self.save(self.asset("GIF"), self.asset("IMAGE"), 1)
        self.assertEqual(saved.status_code, 200, saved.content)
        with patch("pages.startup_views.available_asset", side_effect=CatalogError(
                "素材存储暂不可用。", "MEDIA_STORAGE_UNAVAILABLE", 503)):
            result = self.send(self.client, "post", PREVIEW, {"expectedRevision": saved.json()["data"]["revision"]})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["code"], "MEDIA_STORAGE_UNAVAILABLE")

    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "startup-owner", PASSWORD, display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = self.logged_in(self.owner)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_setting = override_settings(MEDIA_ROOT=self.media.name)
        media_setting.enable()
        self.addCleanup(media_setting.disable)

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

    def asset(self, kind):
        asset_id = uuid.uuid4()
        ext = "gif" if kind == "GIF" else "png"
        path = Path(self.media.name) / f"startup/{asset_id}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(static_png() if kind == "IMAGE" else b"test-media")
        return Asset.objects.create(
            id=asset_id, kind=kind, content_type=f"image/{ext}", byte_size=10,
            width=1, height=1, sha256="0" * 64, original_name=f"brand.{ext}",
            stored_name=f"startup/{asset_id}.{ext}", created_by=self.owner)

    def save(self, gif, fallback, revision):
        return self.send(self.client, "put", DRAFT, {"expectedRevision": revision,
                         "gifAssetId": str(gif.id) if gif else None,
                         "fallbackAssetId": str(fallback.id) if fallback else None})

    def confirmation(self, revision):
        result = self.send(self.client, "post", "/api/v1/admin/auth/confirm",
                           {"action": "startup.publish", "objectId": "startup",
                            "revision": revision, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]["confirmationToken"]

    def publish(self, revision, key="startup-key-123", *, confirmation=True):
        token = self.confirmation(revision) if confirmation else None
        return self.send(self.client, "post", PUBLISH, {"expectedRevision": revision},
                         key=key, confirmation=token)

    def test_permissions_revision_preview_publish_idempotency_and_public_assets(self):
        self.assertEqual(Client().get(PUBLIC).status_code, 404)
        initial = self.client.get(DRAFT).json()["data"]
        self.assertEqual(initial["revision"], 1)
        group = PermissionGroup.objects.create(code="startup-editor-test", name="启动图编辑")
        for code in ("startup.read", "startup.edit"):
            GroupPermission.objects.create(group=group, code=code)
        staff = AdminAccount.objects.create_user("startup-staff", PASSWORD, display_name="启动图编辑")
        staff.permission_groups.add(group)
        staff_client = self.logged_in(staff)
        self.assertEqual(staff_client.get(DRAFT).status_code, 200)
        self.assertEqual(self.send(staff_client, "post", PUBLISH, {"expectedRevision": 1}).status_code, 403)
        gif, fallback = self.asset("GIF"), self.asset("IMAGE")
        saved = self.save(gif, fallback, 1)
        self.assertEqual(saved.status_code, 200, saved.content)
        revision = saved.json()["data"]["revision"]
        self.assertEqual(self.save(gif, fallback, 1).json()["error"]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.send(self.client, "post", PREVIEW, {"expectedRevision": 1}).status_code, 409)
        self.assertEqual(self.send(self.client, "post", PREVIEW, {"expectedRevision": revision}).status_code, 200)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{gif.id}/file").status_code, 404)
        self.assertEqual(self.publish(revision, confirmation=False).status_code, 403)
        published = self.publish(revision)
        self.assertEqual(published.status_code, 200, published.content)
        version_id = published.json()["data"]["versionId"]
        public = Client().get(PUBLIC).json()["data"]
        self.assertEqual(public["versionId"], version_id)
        self.assertEqual(Client().get(public["gifUrl"]).status_code, 200)
        self.assertEqual(Client().get(public["fallbackUrl"]).status_code, 200)
        repeated = self.publish(revision, confirmation=False)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.json()["data"]["versionId"], version_id)
        self.assertEqual(StartupConfigVersion.objects.count(), 1)
        self.assertEqual(StartupPublishRequest.objects.count(), 1)
        self.assertEqual(self.publish(revision, key="startup-key-456").status_code, 409)
        self.assertTrue(AuditLog.objects.filter(action_code="startup.publish").exists())

    def test_failed_replacement_preserves_previous_public_version(self):
        first_gif, first_fallback = self.asset("GIF"), self.asset("IMAGE")
        first = self.save(first_gif, first_fallback, 1).json()["data"]
        first_version = self.publish(first["revision"]).json()["data"]["versionId"]
        next_gif, next_fallback = self.asset("GIF"), self.asset("IMAGE")
        second = self.save(next_gif, next_fallback, first["revision"]).json()["data"]
        (Path(self.media.name) / next_fallback.stored_name).unlink()
        self.assertEqual(self.send(self.client, "post", PREVIEW,
                                   {"expectedRevision": second["revision"]}).status_code, 422)
        self.assertEqual(self.publish(second["revision"], "startup-key-456").status_code, 422)
        self.assertEqual(Client().get(PUBLIC).json()["data"]["versionId"], first_version)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{next_gif.id}/file").status_code, 404)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{first_gif.id}/file").status_code, 200)
        self.assertEqual(StartupConfigVersion.objects.count(), 1)

    def test_invalid_binding_and_database_failure_rollback(self):
        image = self.asset("IMAGE")
        gif = self.asset("GIF")
        self.assertEqual(self.save(image, image, 1).status_code, 400)
        self.assertEqual(self.save(gif, None, 1).status_code, 200)
        self.assertEqual(self.send(self.client, "post", PREVIEW, {"expectedRevision": 2}).status_code, 422)
        saved = self.save(gif, image, 2).json()["data"]
        with patch("pages.startup_views.audit", side_effect=RuntimeError("forced failure")):
            with self.assertRaises(RuntimeError):
                self.publish(saved["revision"])
        self.assertEqual(StartupConfigVersion.objects.count(), 0)
        self.assertEqual(StartupPublishRequest.objects.count(), 0)
        self.assertEqual(Client().get(PUBLIC).status_code, 404)
        self.assertEqual(StartupConfig.objects.get().draft_revision, saved["revision"])

    def test_replacement_revokes_old_public_media_and_read_only_cannot_edit(self):
        group = PermissionGroup.objects.create(code="startup-reader-test", name="启动图只读")
        GroupPermission.objects.create(group=group, code="startup.read")
        reader = AdminAccount.objects.create_user("startup-reader", PASSWORD, display_name="启动图只读")
        reader.permission_groups.add(group)
        reader_client = self.logged_in(reader)
        self.assertEqual(reader_client.get(DRAFT).status_code, 200)
        self.assertEqual(self.send(reader_client, "put", DRAFT, {"expectedRevision": 1,
                         "gifAssetId": None, "fallbackAssetId": None}).status_code, 403)
        first_gif, first_fallback = self.asset("GIF"), self.asset("IMAGE")
        first = self.save(first_gif, first_fallback, 1).json()["data"]
        self.assertEqual(self.publish(first["revision"]).status_code, 200)
        second_gif, second_fallback = self.asset("GIF"), self.asset("IMAGE")
        second = self.save(second_gif, second_fallback, first["revision"]).json()["data"]
        self.assertEqual(self.publish(second["revision"], "startup-key-456").status_code, 200)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{first_gif.id}/file").status_code, 404)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{first_fallback.id}/file").status_code, 404)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{second_gif.id}/file").status_code, 200)
        self.assertEqual(Client().get(f"/api/v1/app/assets/{second_fallback.id}/file").status_code, 200)
        self.assertEqual(StartupConfigVersion.objects.count(), 2)

    def test_gif_upload_decodes_frames_and_rejects_mismatched_kind(self):
        gif_path = Path(self.media.name) / "animated.gif"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=size=4x4:rate=2", "-frames:v", "2", "-y", str(gif_path)],
                       check=True, timeout=15)
        payload = gif_path.read_bytes()
        headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}
        uploaded = self.client.post("/api/v1/admin/assets", {"kind": "GIF",
            "file": SimpleUploadedFile("brand.gif", payload, content_type="image/gif")}, **headers)
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        data = uploaded.json()["data"]
        self.assertEqual((data["kind"], data["contentType"]), ("GIF", "image/gif"))
        self.assertEqual((data["width"], data["height"]), (4, 4))
        self.assertEqual(self.client.get(data["adminUrl"]).status_code, 200)
        invalid = self.client.post("/api/v1/admin/assets", {"kind": "IMAGE",
            "file": SimpleUploadedFile("brand.gif", payload, content_type="image/gif")}, **headers)
        self.assertEqual(invalid.status_code, 400)
        malformed = self.client.post("/api/v1/admin/assets", {"kind": "GIF",
            "file": SimpleUploadedFile("broken.gif", b"GIF89a" + b"\x00" * 16,
                                       content_type="image/gif")}, **headers)
        self.assertEqual(malformed.status_code, 400)
        single_path = Path(self.media.name) / "single.gif"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=size=4x4:rate=1", "-frames:v", "1", "-y", str(single_path)],
                       check=True, timeout=15)
        single = self.client.post("/api/v1/admin/assets", {"kind": "GIF",
            "file": SimpleUploadedFile("single.gif", single_path.read_bytes(),
                                       content_type="image/gif")}, **headers)
        self.assertEqual(single.status_code, 400)

    def test_apng_is_not_a_static_fallback_even_if_previously_stored(self):
        apng_path = Path(self.media.name) / "animated.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=size=4x4:rate=2", "-frames:v", "2", "-plays", "0",
                        "-f", "apng", "-y", str(apng_path)], check=True, timeout=15)
        payload = apng_path.read_bytes()
        self.assertIn(b"acTL", payload)
        headers = {"HTTP_X_CSRFTOKEN": self.client.cookies["csrftoken"].value}
        uploaded = self.client.post("/api/v1/admin/assets", {"kind": "IMAGE",
            "file": SimpleUploadedFile("animated.png", payload, content_type="image/png")}, **headers)
        self.assertEqual(uploaded.status_code, 400, uploaded.content)
        escaped = self.asset("IMAGE")
        (Path(self.media.name) / escaped.stored_name).write_bytes(payload)
        gif = self.asset("GIF")
        self.assertEqual(self.save(gif, escaped, 1).status_code, 400)
