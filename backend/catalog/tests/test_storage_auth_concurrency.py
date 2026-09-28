"""Real PostgreSQL asset-row waits must not retain stale delete authority."""
import tempfile
import threading
import time
import uuid
from pathlib import Path

from django.contrib.sessions.backends.db import SessionStore
from django.db import close_old_connections, connection, transaction
from django.test import RequestFactory, TransactionTestCase, override_settings

from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.media import delete_unbound_asset
from catalog.models import Asset
from catalog.validation import CatalogError


class StorageAuthorizationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        setting = override_settings(MEDIA_ROOT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        self.actor = AdminAccount.objects.create_user("asset-staff", "Asset staff test passphrase 2026!", kind="STAFF")
        group = PermissionGroup.objects.create(code="asset-concurrency", name="素材操作")
        self.permission = GroupPermission.objects.create(group=group, code="asset.delete")
        AccountGroup.objects.create(account=self.actor, group=group)
        path = self.root / "product/auth.png"
        path.parent.mkdir()
        path.write_bytes(b"private image")
        self.asset = Asset.objects.create(kind="IMAGE", content_type="image/png", byte_size=13,
            width=1, height=1, sha256="0" * 64, original_name="auth.png", stored_name="product/auth.png",
            created_by=self.actor)
        self.path = path

    def delete_after_authority_changes(self, change):
        started = threading.Event()
        backend_pids, results, failures = [], [], []
        def worker():
            close_old_connections()
            try:
                connection.ensure_connection()
                backend_pids.append(connection.connection.info.backend_pid)
                actor = AdminAccount.objects.get(pk=self.actor.pk)
                request = RequestFactory().delete("/api/v1/admin/assets")
                request.request_id = uuid.uuid4()
                request.user = actor
                request.session = SessionStore()
                request.session["admin_auth_version"] = actor.auth_version
                request.session["admin_last_active"] = time.time()
                started.set()
                try:
                    delete_unbound_asset(request, self.asset.pk, actor)
                    results.append(200)
                except CatalogError as exc:
                    results.append(exc.status)
            except Exception as exc:
                failures.append(exc)
            finally:
                connection.close()
        thread = threading.Thread(target=worker, name="asset-delete-authority")
        try:
            with transaction.atomic():
                Asset.objects.select_for_update().get(pk=self.asset.pk)
                thread.start()
                self.assertTrue(started.wait(5))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [backend_pids[0]])
                        row = cursor.fetchone()
                    if row and row[0] == "Lock":
                        break
                    time.sleep(0.02)
                else:
                    self.fail("delete request did not wait for the asset row lock")
                change()
        finally:
            if thread.ident is not None:
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures, failures)
        self.assertTrue(Asset.objects.filter(pk=self.asset.pk).exists())
        self.assertEqual(self.path.read_bytes(), b"private image")
        return results

    def test_disabled_account_after_asset_lock_wait_is_rejected(self):
        results = self.delete_after_authority_changes(
            lambda: AdminAccount.objects.filter(pk=self.actor.pk).update(enabled=False))
        self.assertEqual(results, [401])

    def test_revoked_permission_after_asset_lock_wait_is_rejected(self):
        results = self.delete_after_authority_changes(lambda: self.permission.delete())
        self.assertEqual(results, [403])
