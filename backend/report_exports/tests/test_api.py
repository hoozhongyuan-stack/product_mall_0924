"""Export API authorization, idempotency and private-file boundaries."""

import hashlib
import json
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount, GroupPermission, PermissionGroup
from report_exports.models import ExportTask


class ExportApiTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "export-owner", "Synthetic password 2026!", display_name="主账号", kind="OWNER")
        self.staff = AdminAccount.objects.create_user(
            "export-staff", "Synthetic password 2026!", display_name="员工", kind="STAFF")
        self.other = AdminAccount.objects.create_user(
            "export-other", "Synthetic password 2026!", display_name="其他员工", kind="STAFF")
        self.group = PermissionGroup.objects.create(code="export-business", name="经营导出")
        self.staff.permission_groups.add(self.group)
        self.read_grant = GroupPermission.objects.create(group=self.group, code="business.report.read")
        self.export_grant = GroupPermission.objects.create(group=self.group, code="business.report.export")
        self.client = self._signed_client(self.owner)

    @staticmethod
    def _signed_client(account, *, csrf=False):
        client = Client(enforce_csrf_checks=csrf)
        client.force_login(account)
        session = client.session
        session["admin_auth_version"] = account.auth_version
        session["admin_last_active"] = time.time()
        session.save()
        return client

    @staticmethod
    def _body(kind="BUSINESS", filters=None, request_key=None):
        return {"kind": kind, "filters": filters if filters is not None else
                {"from": "2026-09-01", "to": "2026-09-02"},
                "requestKey": request_key or str(uuid.uuid4())}

    @staticmethod
    def _post(client, body):
        return client.post("/api/v1/admin/exports", data=json.dumps(body), content_type="application/json")

    def test_create_normalizes_filters_and_same_key_is_idempotent_but_conflict_is_rejected(self):
        body = self._body(kind="AUDIT", filters={"from": "2026-09-01", "to": "2026-09-02",
                                                 "actorId": str(self.staff.pk).upper(),
                                                 "actionCode": "catalog.update"})
        first = self._post(self.client, body)
        self.assertEqual(first.status_code, 201, first.content)
        task = first.json()["data"]
        self.assertEqual(task["requestKey"], body["requestKey"])
        self.assertEqual(task["filters"]["actorId"], str(self.staff.pk))
        repeated = self._post(self.client, body)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.json()["data"]["taskId"], task["taskId"])
        conflicting = self._post(self.client, {**body, "filters": {**body["filters"], "to": "2026-09-03"}})
        self.assertEqual(conflicting.status_code, 409)
        self.assertEqual(ExportTask.objects.count(), 1)

    def test_strict_filters_and_both_export_permissions(self):
        for filters in ({"from": "2026-09-01", "to": "2026-09-02", "unknown": "x"},
                        {"from": "2026-09-01", "to": "2026-09-02", "actorId": "invalid"},
                        {"from": "2026-01-01", "to": "2026-09-02"},
                        {"from": ["2026-09-01"], "to": "2026-09-02"}):
            kind = "AUDIT" if "actorId" in filters else "BUSINESS"
            self.assertEqual(self._post(self.client, self._body(kind=kind, filters=filters)).status_code, 400)
        staff_client = self._signed_client(self.staff)
        self.assertEqual(self._post(staff_client, self._body(kind="AUDIT")).status_code, 403)
        allowed = self._post(staff_client, self._body())
        self.assertEqual(allowed.status_code, 201, allowed.content)
        self.export_grant.delete()
        self.assertEqual(staff_client.get("/api/v1/admin/exports/" + allowed.json()["data"]["taskId"]).status_code, 403)

    def test_list_cursor_is_owner_bound_and_other_account_gets_404(self):
        ids = []
        for _ in range(3):
            created = self._post(self.client, self._body())
            self.assertEqual(created.status_code, 201, created.content)
            ids.append(created.json()["data"]["taskId"])
        page = self.client.get("/api/v1/admin/exports", {"kind": "BUSINESS", "limit": "2"})
        self.assertEqual(page.status_code, 200, page.content)
        self.assertEqual(len(page.json()["data"]["items"]), 2)
        cursor = page.json()["data"]["nextCursor"]
        self.assertTrue(cursor)
        second = self.client.get("/api/v1/admin/exports", {"kind": "BUSINESS", "limit": "2", "cursor": cursor})
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(len(second.json()["data"]["items"]), 1)
        self.assertEqual(self.client.get("/api/v1/admin/exports", {"kind": "AUDIT", "cursor": cursor}).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/admin/exports?kind=BUSINESS&kind=AUDIT").status_code, 400)
        other_client = self._signed_client(self.other)
        self.assertEqual(other_client.get("/api/v1/admin/exports/" + ids[0]).status_code, 404)
        self.assertEqual(other_client.get("/api/v1/admin/exports/" + ids[0] + "/download").status_code, 404)

    def test_ready_download_rechecks_permission_expiry_and_file_integrity(self):
        staff_client = self._signed_client(self.staff)
        created = self._post(staff_client, self._body())
        self.assertEqual(created.status_code, 201, created.content)
        task_id = created.json()["data"]["taskId"]
        endpoint = f"/api/v1/admin/exports/{task_id}/download"
        self.assertEqual(staff_client.get(endpoint).status_code, 409)
        payload = b"date,paidAmountFen\n2026-09-01,5\n"
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            task = ExportTask.objects.get(pk=task_id)
            key = f"export/{task.pk}.csv"
            path = Path(directory) / key
            path.parent.mkdir(mode=0o700)
            path.write_bytes(payload)
            task.status = "READY"
            task.object_key = key
            task.file_sha256 = hashlib.sha256(payload).hexdigest()
            task.file_bytes = len(payload)
            task.completed_at = timezone.now()
            task.expires_at = task.completed_at + timedelta(hours=24)
            task.save()
            ready = staff_client.get(endpoint)
            self.assertEqual(ready.status_code, 200)
            self.assertIn("private", ready["Cache-Control"])
            self.assertIn("no-store", ready["Cache-Control"])
            self.assertEqual(b"".join(ready.streaming_content), payload)
            self.export_grant.delete()
            self.assertEqual(staff_client.get(endpoint).status_code, 403)
            self.export_grant = GroupPermission.objects.create(group=self.group, code="business.report.export")
            path.write_bytes(b"tampered")
            self.assertEqual(staff_client.get(endpoint).status_code, 503)
            path.write_bytes(payload)
            task.expires_at = timezone.now() - timedelta(seconds=1)
            task.save(update_fields=["expires_at"])
            self.assertEqual(staff_client.get(endpoint).status_code, 410)

    def test_post_requires_csrf_token(self):
        csrf_client = self._signed_client(self.owner, csrf=True)
        self.assertEqual(self._post(csrf_client, self._body()).status_code, 403)
        self.assertEqual(ExportTask.objects.count(), 0)
