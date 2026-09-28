"""Real material row-lock waits must not preserve revoked write authority."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from accounts.models import AdminAccount, GroupPermission
from catalog.models import Asset
from catalog.tests.test_asset_references import AssetFixture, config
from catalog.page_targets import assets_exist
from pages.models import PageDraftAsset, StartupConfig, StartupPublication
from pages.startup_views import _assets


class AssetLockAuthorityTests(AssetFixture, TransactionTestCase):
    def wait_for_row_lock(self, pid):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pid])
                row = cursor.fetchone()
            if row and row[0] == "Lock":
                return
            time.sleep(.02)
        self.fail("request did not wait on the held material row lock")

    def blocked_write(self, domain, revoked):
        permission = f"{domain}.edit"
        actor, group, client = self.staff([permission, "asset.upload"])
        asset = self.asset(actor)
        entered = Event()
        pids = []
        if domain == "page":
            target = self.page()
            path = f"/api/v1/admin/pages/{target.pk}/draft"
            payload = {"name": target.name, "expectedRevision": target.draft_revision, "config": config(asset.pk)}
            original, patch_path = assets_exist, "pages.views.assets_exist"
        else:
            target, _ = StartupConfig.objects.get_or_create(pk=1)
            StartupPublication.objects.get_or_create(config=target)
            path = "/api/v1/admin/startup/draft"
            payload = {"expectedRevision": target.draft_revision, "gifAssetId": None,
                       "fallbackAssetId": str(asset.pk)}
            original, patch_path = _assets, "pages.startup_views._assets"
        revision = target.draft_revision

        def gated(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.append(cursor.fetchone()[0])
            entered.set()
            return original(*args, **kwargs)

        def write():
            try:
                return client.put(path, data=json.dumps(payload), content_type="application/json",
                                  HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
            finally:
                connections.close_all()

        with patch(patch_path, side_effect=gated), ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Asset.objects.select_for_update().get(pk=asset.pk)
                future = pool.submit(write)
                self.assertTrue(entered.wait(10), "request did not reach material lookup")
                self.wait_for_row_lock(pids[0])
                if revoked == "permission":
                    GroupPermission.objects.filter(group=group, code=permission).delete()
                elif revoked == "disable":
                    AdminAccount.objects.filter(pk=actor.pk).update(enabled=False)
                else:
                    AdminAccount.objects.filter(pk=actor.pk).update(auth_version=actor.auth_version + 1)
            result = future.result(timeout=15)
        self.assertEqual(result.status_code, 403 if revoked == "permission" else 401, result.content)
        target.refresh_from_db()
        self.assertEqual(target.draft_revision, revision)
        if domain == "page":
            self.assertEqual(target.draft_config, config())
            self.assertFalse(PageDraftAsset.objects.filter(page=target).exists())
        else:
            self.assertIsNone(target.gif_asset_id)
            self.assertIsNone(target.fallback_asset_id)

    def test_page_permission_revoked_during_asset_lock_wait(self):
        self.blocked_write("page", "permission")

    def test_startup_permission_revoked_during_asset_lock_wait(self):
        self.blocked_write("startup", "permission")

    def test_page_account_disabled_during_asset_lock_wait(self):
        self.blocked_write("page", "disable")

    def test_startup_account_disabled_during_asset_lock_wait(self):
        self.blocked_write("startup", "disable")

    def test_page_auth_version_changed_during_asset_lock_wait(self):
        self.blocked_write("page", "version")

    def test_startup_auth_version_changed_during_asset_lock_wait(self):
        self.blocked_write("startup", "version")
