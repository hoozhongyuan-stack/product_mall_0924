"""Publication fences and fresh authority across real PostgreSQL connections."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase

from accounts.models import AdminAccount, GroupPermission, PermissionGroup
from pages.models import StorefrontConfig, StorefrontPublication
from pages.storefront_views import _config
from pages.storefront_validation import defaults


PASSWORD = "Safe owner passphrase 2026!"
PATH = "/api/v1/admin/navigation/publish"


class StorefrontConcurrencyTests(TransactionTestCase):
    def setUp(self):
        for domain in ("navigation", "customer_service"):
            config, _ = StorefrontConfig.objects.get_or_create(
                domain=domain, defaults={"draft_config": defaults(domain)})
            StorefrontPublication.objects.get_or_create(config=config)
        self.actor = AdminAccount.objects.create_user("storefront-concurrent", PASSWORD, kind="STAFF")
        self.group = PermissionGroup.objects.create(code="storefront-concurrent", name="导航发布")
        self.actor.permission_groups.add(self.group)
        for code in ("navigation.read", "navigation.publish"):
            GroupPermission.objects.create(group=self.group, code=code)

    def logged_in(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        self.post(client, "/api/v1/admin/auth/login", {
            "loginName": self.actor.login_name, "password": PASSWORD})
        return client

    def post(self, client, path, body, *, key=None, token=None):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        if token:
            headers["HTTP_X_ACTION_CONFIRMATION"] = token
        return client.post(path, data=json.dumps(body), content_type="application/json", **headers)

    def prepared(self):
        client = self.logged_in()
        confirmed = self.post(client, "/api/v1/admin/auth/confirm", {
            "action": "navigation.publish", "objectId": "navigation", "revision": 1,
            "password": PASSWORD})
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return client, confirmed.json()["data"]["confirmationToken"]

    def execute(self, prepared, key):
        client, token = prepared
        try:
            return self.post(client, PATH, {"expectedRevision": 1, "expectedPublicationRevision": 0},
                             key=key, token=token)
        finally:
            connections.close_all()

    def test_two_publishers_same_epoch_only_one_succeeds(self):
        first, second = self.prepared(), self.prepared()
        barrier = threading.Barrier(2, timeout=10)

        def together(domain, *, lock=False):
            if lock:
                barrier.wait()
            return _config(domain, lock=lock)

        with patch("pages.storefront_views._config", side_effect=together), ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(self.execute, first, "nav-concurrent-first"),
                       pool.submit(self.execute, second, "nav-concurrent-second")]
            results = [future.result(timeout=20) for future in pending]
        self.assertEqual(sorted(row.status_code for row in results), [200, 409])
        self.assertEqual(StorefrontPublication.objects.get(config_id="navigation").revision, 1)

    def test_permission_revoked_while_waiting_for_domain_lock(self):
        prepared = self.prepared()
        started = threading.Event()
        pids = []

        def execute():
            try:
                connection.ensure_connection()
                pids.append(connection.connection.info.backend_pid)
                started.set()
                return self.execute(prepared, "nav-revoked-while-waiting")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                _config("navigation", lock=True)
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
                    self.fail("request did not wait for domain row lock")
                GroupPermission.objects.filter(group=self.group, code="navigation.publish").delete()
            result = future.result(timeout=15)
        self.assertEqual(result.status_code, 403, result.content)
        self.assertEqual(StorefrontPublication.objects.get(config_id="navigation").revision, 0)
