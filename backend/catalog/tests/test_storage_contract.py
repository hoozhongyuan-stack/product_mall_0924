"""E0 local storage boundary, failure and recovery contracts."""
import os
import subprocess
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount
from catalog.media import asset_path, available_asset, delete_unbound_asset, lock_available_assets, purge_expired_orphans, serve_asset, store_asset
from catalog.models import Asset
from catalog.storage import LocalStorage, recover_media_storage
from catalog.validation import CatalogError
from .test_media_flow import png


class StorageBoundaryTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.storage = LocalStorage(self.root)

    def test_namespaces_are_private_and_isolated(self):
        for namespace in ("media", "code", "export"):
            self.assertEqual(self.storage.policy(namespace)["visibility"], "private")
            self.assertTrue(self.storage.key(namespace, "a.png").startswith(namespace + "/"))
        with self.assertRaises(CatalogError):
            self.storage.key("other", "a.png")

    def test_path_rejects_traversal_absolute_and_symlink(self):
        for key in ("../outside", "/tmp/outside", "media/../../outside", "media\\outside"):
            with self.assertRaises(CatalogError):
                self.storage.path(key)
        (self.root / "media").mkdir()
        (self.root / "media/link").symlink_to(self.root / "media", target_is_directory=True)
        with self.assertRaises(CatalogError):
            self.storage.path("media/link/item.png")

    def test_promote_never_overwrites_an_existing_object(self):
        temporary = self.storage.temporary()
        temporary.write_bytes(b"original")
        first = self.storage.promote(temporary, "code/release/package.zip")
        next_temporary = self.storage.temporary()
        next_temporary.write_bytes(b"replacement")
        with self.assertRaises(FileExistsError):
            self.storage.promote(next_temporary, "code/release/package.zip")
        self.assertEqual(first.read_bytes(), b"original")

    def test_legacy_keys_remain_readable(self):
        self.assertEqual(self.storage.path("product/aa/legacy.png"), self.root / "product/aa/legacy.png")


class StorageConsistencyTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        setting = override_settings(MEDIA_ROOT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        self.actor = AdminAccount.objects.create_user("storage_owner", "Storage test passphrase 2026!", kind="OWNER")
        self.request = RequestFactory().post("/api/v1/admin/assets")
        self.request.request_id = uuid.uuid4()
        self.request.user = self.actor
        self.request.session = {"admin_auth_version": self.actor.auth_version,
                                "admin_last_active": time.time()}

    def upload(self):
        with patch("catalog.media.verify_decodable"):
            return store_asset(self.request, SimpleUploadedFile("image.png", png(), content_type="image/png"), "IMAGE", self.actor)

    def expire(self, asset):
        Asset.objects.filter(pk=asset.pk).update(created_at=timezone.now() - timedelta(days=2))

    def test_write_failure_is_503_and_has_no_record(self):
        with patch("pathlib.Path.mkdir", side_effect=OSError("disk full")):
            with self.assertRaises(CatalogError) as raised:
                self.upload()
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(Asset.objects.count(), 0)

    def test_outer_rollback_preserves_asset_file_and_recovery_cancels_marker(self):
        asset = self.upload()
        path = asset_path(asset)
        self.expire(asset)
        try:
            with transaction.atomic():
                purge_expired_orphans()
                raise RuntimeError("caller failed")
        except RuntimeError:
            pass
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(path.read_bytes(), png())
        with self.captureOnCommitCallbacks(execute=True):
            recover_media_storage()
        self.assertTrue(path.exists())
        self.assertEqual(list((self.root / ".pending-delete").glob("*.json")), [])

    def test_committed_deletion_failure_is_recoverable(self):
        asset = self.upload()
        path = asset_path(asset)
        self.expire(asset)
        original = Path.unlink
        def failing_unlink(candidate, *args, **kwargs):
            if candidate == path:
                raise OSError("temporarily unavailable")
            return original(candidate, *args, **kwargs)
        with patch("pathlib.Path.unlink", failing_unlink), self.captureOnCommitCallbacks(execute=True):
            purge_expired_orphans()
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())
        self.assertTrue(path.exists())
        self.assertGreaterEqual(len(list((self.root / ".pending-delete").glob("*.json"))), 1)
        with self.captureOnCommitCallbacks(execute=True):
            recover_media_storage()
        self.assertFalse(path.exists())

    def test_recovery_removes_only_stale_temporary_files(self):
        temporary = self.root / "tmp"
        temporary.mkdir()
        stale, recent = temporary / "stale.upload", temporary / "recent.upload"
        stale.write_bytes(b"interrupted")
        recent.write_bytes(b"active")
        old = (timezone.now() - timedelta(days=2)).timestamp()
        os.utime(stale, (old, old))
        with self.captureOnCommitCallbacks(execute=True):
            recover_media_storage()
        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())

    def test_upload_outer_rollback_leaves_recoverable_intent_and_no_record(self):
        try:
            with transaction.atomic():
                asset = self.upload()
                path = asset_path(asset)
                raise RuntimeError("outer caller failed")
        except RuntimeError:
            pass
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())
        self.assertTrue(path.exists())
        with self.captureOnCommitCallbacks(execute=True):
            recover_media_storage()
        self.assertFalse(path.exists())

    def test_delete_audit_failure_preserves_record_and_file(self):
        asset = self.upload()
        with patch("catalog.media.audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                delete_unbound_asset(self.request, asset.pk, self.actor)
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(asset_path(asset).read_bytes(), png())
        with self.captureOnCommitCallbacks(execute=True):
            recover_media_storage()
        self.assertTrue(asset_path(asset).exists())

    def test_binding_rejects_expired_missing_and_unsafe_files(self):
        asset = self.upload()
        self.assertEqual(lock_available_assets([asset.id])[asset.id], asset)
        self.expire(asset)
        asset.refresh_from_db()
        self.assertFalse(available_asset(asset))
        with self.assertRaises(CatalogError):
            lock_available_assets([asset.id])
        asset.created_at = timezone.now()
        asset_path(asset).unlink()
        self.assertFalse(available_asset(asset))
        asset.stored_name = "../outside"
        self.assertFalse(available_asset(asset))

    def test_namespace_does_not_allow_code_to_be_media(self):
        asset = self.upload()
        asset.stored_name = "code/private.zip"
        with self.assertRaises(CatalogError):
            asset_path(asset)

    def test_database_failure_leaves_no_usable_record_or_file(self):
        with patch("catalog.media.Asset.objects.create", side_effect=RuntimeError("db unavailable")):
            with self.assertRaises(RuntimeError):
                self.upload()
        self.assertEqual(Asset.objects.count(), 0)
        self.assertEqual(list(self.root.rglob("*.png")), [])
        with self.captureOnCommitCallbacks(execute=True):
            recover_media_storage()
        self.assertEqual(list((self.root / ".pending-delete").glob("*.json")), [])

    def test_invalid_recovery_marker_is_retained_without_deleting_private_code(self):
        pending = self.root / ".pending-delete"
        pending.mkdir()
        code = self.root / "code/private.zip"
        code.parent.mkdir()
        code.write_bytes(b"private")
        marker = pending / "bad.json"
        marker.write_text('{"assetId":"' + str(uuid.uuid4()) + '","key":"code/private.zip"}')
        with self.assertLogs("catalog.storage", level="WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                recover_media_storage()
        self.assertTrue(code.exists())
        self.assertTrue(marker.exists())

    def test_multiple_uploads_then_outer_rollback_keep_all_recovery_intents(self):
        paths = []
        try:
            with transaction.atomic():
                first = self.upload()
                paths.append(asset_path(first))
                second = self.upload()
                paths.append(asset_path(second))
                raise RuntimeError("outer rollback")
        except RuntimeError:
            pass
        self.assertEqual(Asset.objects.count(), 0)
        self.assertTrue(all(path.exists() for path in paths))
        with self.captureOnCommitCallbacks(execute=True):
            recover_media_storage()
        self.assertTrue(all(not path.exists() for path in paths))

    def test_repeated_purge_then_outer_rollback_does_not_delete_file(self):
        asset = self.upload()
        self.expire(asset)
        path = asset_path(asset)
        try:
            with transaction.atomic():
                purge_expired_orphans()
                purge_expired_orphans()
                raise RuntimeError("outer rollback")
        except RuntimeError:
            pass
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(path.read_bytes(), png())

    def test_actual_jpeg_upload_has_correct_metadata_and_bytes(self):
        encoded = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "1",
            "-f", "image2pipe", "-i", "pipe:0", "-frames:v", "1", "-c:v", "mjpeg", "-threads", "1",
            "-f", "image2pipe", "pipe:1"], input=png(2, 2), capture_output=True, check=True).stdout
        asset = store_asset(self.request,
            SimpleUploadedFile("image.jpg", encoded, content_type="image/jpeg"), "IMAGE", self.actor)
        self.assertEqual(asset.content_type, "image/jpeg")
        self.assertEqual((asset.width, asset.height), (2, 2))
        served = serve_asset(RequestFactory().get("/asset"), asset)
        try:
            self.assertEqual(b"".join(served.streaming_content), encoded)
        finally:
            served.file_to_stream.close()

    def test_video_ranges_return_exact_bytes_and_reject_unsatisfiable_requests(self):
        asset = self.upload()
        asset.kind = "VIDEO"
        payload = png()
        for header, expected in (("bytes=2-5", payload[2:6]), ("bytes=-4", payload[-4:]),
                                 ("bytes=7-", payload[7:])):
            served = serve_asset(RequestFactory().get("/asset", HTTP_RANGE=header), asset)
            self.assertEqual(served.status_code, 206)
            self.assertEqual(b"".join(served.streaming_content), expected)
        for header in ("bytes=999999-", "bytes=5-2", "bytes=0-1,3-4", "bytes=-", "items=0-1"):
            served = serve_asset(RequestFactory().get("/asset", HTTP_RANGE=header), asset)
            self.assertEqual(served.status_code, 416)
            self.assertEqual(served["Content-Range"], f"bytes */{len(payload)}")

    def test_file_read_outage_is_a_controlled_503(self):
        asset = self.upload()
        with patch("pathlib.Path.open", side_effect=OSError("read unavailable")):
            with self.assertRaises(CatalogError) as raised:
                serve_asset(RequestFactory().get("/asset"), asset)
        self.assertEqual(raised.exception.status, 503)

    def test_recovery_management_command_runs_and_surfaces_storage_failure(self):
        from io import StringIO
        output = StringIO()
        call_command("recover_media_storage", stdout=output)
        self.assertIn("Recovered", output.getvalue())
        with patch("catalog.management.commands.recover_media_storage.recover_media_storage",
                   side_effect=CatalogError("存储不可用", "MEDIA_STORAGE_UNAVAILABLE", 503)):
            with self.assertRaises(CommandError):
                call_command("recover_media_storage", stdout=StringIO())

    def test_mismatched_intent_never_deletes_another_asset_file(self):
        asset = self.upload()
        path = asset_path(asset)
        marker = LocalStorage().stage_intent(uuid.uuid4(), asset.stored_name)
        with self.assertLogs("catalog.storage", level="WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                recover_media_storage()
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(path.read_bytes(), png())
        self.assertTrue(LocalStorage().path(marker).exists())
