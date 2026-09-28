"""PostgreSQL publication epochs fence publish/rollback and revoked authority."""
import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase, override_settings

from accounts.models import AdminAccount, GroupPermission, PermissionGroup
from catalog.models import Asset
from pages.models import MicroPage, PageConfigVersion, PagePublication, PageVersionAsset
from pages.views import _lock_publication_graph
from .test_home_flow import PASSWORD


class HistoryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        setting = override_settings(MEDIA_ROOT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        self.actor = AdminAccount.objects.create_user("history-concurrent", PASSWORD, kind="STAFF")
        self.group = PermissionGroup.objects.create(code="history-concurrent", name="发布")
        self.actor.permission_groups.add(self.group)
        for code in ("page.read", "page.publish"):
            GroupPermission.objects.create(group=self.group, code=code)
        config = {"schemaVersion": 1, "pageType": "MICRO", "theme": {
            "pageBackgroundColor": "#FFFFFF", "headerBackgroundColor": "#FFFFFF", "brandTextColor": "#222222"},
            "components": []}
        self.page = MicroPage.objects.create(page_type="MICRO", name="并发", draft_revision=3, draft_config=config)
        self.a = PageConfigVersion.objects.create(page=self.page, revision=1, name="旧版", config_json=config,
                                                  published_by=self.actor)
        self.b = PageConfigVersion.objects.create(page=self.page, revision=2, name="新版", config_json=config,
                                                  published_by=self.actor)
        self.publication = PagePublication.objects.create(page=self.page, current_version=self.a, revision=1)
        self.publication.current_version = self.b
        self.publication.revision = 2
        self.publication.save()

    def logged_in(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.post(client, "/api/v1/admin/auth/login", {"loginName": self.actor.login_name, "password": PASSWORD})
        self.assertEqual(result.status_code, 200)
        return client

    def post(self, client, path, body, *, token=None, key=None):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if token:
            headers["HTTP_X_ACTION_CONFIRMATION"] = token
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return client.post(path, data=json.dumps(body), content_type="application/json", **headers)

    def request(self, action):
        client = self.logged_in()
        rollback = action == "rollback"
        confirmed = self.post(client, "/api/v1/admin/auth/confirm", {"action": "page." + action,
            "password": PASSWORD, "objectId": str(self.page.pk) + (":" + str(self.a.pk) if rollback else ""),
            "revision": 2 if rollback else 3})
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        body = {"expectedRevision": 3, "expectedPublicationRevision": 2}
        if rollback:
            body = {**body, "versionId": str(self.a.pk), "reason": "恢复"}
        return client, f"/api/v1/admin/pages/{self.page.pk}/" + action, body, confirmed.json()["data"]["confirmationToken"]

    def execute(self, request, key):
        try:
            client, path, body, token = request
            return self.post(client, path, body, token=token, key=key)
        finally:
            connections.close_all()

    def test_publish_and_rollback_same_epoch_exactly_one_success(self):
        publish, rollback = self.request("publish"), self.request("rollback")
        barrier = threading.Barrier(2, timeout=10)
        def together():
            barrier.wait()
            _lock_publication_graph()
        with patch("pages.views._lock_publication_graph", side_effect=together), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.execute, publish, "e1-concurrent-publish"),
                       pool.submit(self.execute, rollback, "e1-concurrent-rollback")]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(item.status_code for item in results), [200, 409])
        self.assertEqual([item.json()["error"]["code"] for item in results if item.status_code == 409],
                         ["PUBLICATION_REVISION_CONFLICT"])
        self.publication.refresh_from_db()
        self.page.refresh_from_db()
        self.assertEqual(self.publication.revision, 3)
        self.assertEqual(self.page.draft_revision, 3)

    def blocked(self, request, change, *, lock_asset=None):
        started = threading.Event()
        pids = []
        def execute():
            try:
                connection.ensure_connection()
                pids.append(connection.connection.info.backend_pid)
                started.set()
                return self.execute(request, "e1-blocked-operation")
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                if lock_asset is None:
                    _lock_publication_graph()
                else:
                    Asset.objects.select_for_update().get(pk=lock_asset.pk)
                future = pool.submit(execute)
                self.assertTrue(started.wait(5))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pids[0]])
                        row = cursor.fetchone()
                    if row and row[0] == "Lock":
                        break
                    time.sleep(.02)
                else:
                    self.fail("request did not wait for PostgreSQL lock")
                change()
            return future.result(timeout=15)

    def test_rollback_rejects_permission_revoked_after_graph_lock_wait(self):
        result = self.blocked(self.request("rollback"), lambda:
            GroupPermission.objects.filter(group=self.group, code="page.publish").delete())
        self.assertEqual(result.status_code, 403, result.content)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.current_version_id, self.b.pk)
        self.assertEqual(self.publication.revision, 2)

    def test_rollback_rejects_disabled_account_after_graph_lock_wait(self):
        result = self.blocked(self.request("rollback"), lambda:
            AdminAccount.objects.filter(pk=self.actor.pk).update(enabled=False))
        self.assertEqual(result.status_code, 401, result.content)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.current_version_id, self.b.pk)

    def test_successful_publish_replay_revalidates_permission_after_lock_wait(self):
        request = self.request("publish")
        client, path, body, token = request
        success = self.post(client, path, body, token=token, key="e1-blocked-operation")
        self.assertEqual(success.status_code, 200, success.content)
        result = self.blocked(request, lambda:
            GroupPermission.objects.filter(group=self.group, code="page.publish").delete())
        self.assertEqual(result.status_code, 403, result.content)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.revision, 3)

    def historical_image(self):
        import uuid
        asset_id = uuid.uuid4()
        stored_name = f"page/{asset_id}.png"
        path = self.root / stored_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-image")
        asset = Asset.objects.create(id=asset_id, kind="IMAGE", content_type="image/png", byte_size=10,
            width=1, height=1, sha256="0" * 64, original_name="history.png", stored_name=stored_name,
            created_by=self.actor)
        config = {**self.page.draft_config, "components": [{"componentId": "hero", "type": "CAROUSEL",
            "sortOrder": 10, "visible": True, "props": {"slides": [{"assetId": str(asset_id)}]}}]}
        self.a = PageConfigVersion.objects.create(page=self.page, revision=4, name="素材旧版",
            config_json=config, published_by=self.actor)
        PageVersionAsset.objects.create(version=self.a, asset=asset, is_public=True)
        from catalog.media import available_asset
        self.assertTrue(available_asset(asset))
        return asset, path

    def test_rollback_rechecks_permission_after_asset_lock_wait(self):
        asset, _ = self.historical_image()
        result = self.blocked(self.request("rollback"), lambda:
            GroupPermission.objects.filter(group=self.group, code="page.publish").delete(), lock_asset=asset)
        self.assertEqual(result.status_code, 403, result.content)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.current_version_id, self.b.pk)

    def test_rollback_rechecks_file_after_asset_lock_wait(self):
        asset, path = self.historical_image()
        result = self.blocked(self.request("rollback"), path.unlink, lock_asset=asset)
        self.assertEqual(result.status_code, 422, result.content)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.revision, 2)
