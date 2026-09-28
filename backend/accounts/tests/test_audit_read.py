import uuid
from datetime import datetime, timedelta, timezone as utc_timezone
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AdminAccount, AuditLog, GroupPermission, PermissionGroup
from accounts.read_window import read_window
from accounts.audit_read import _safe_json


class AuditReadTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user("audit-owner", "Synthetic password 2026!",
            display_name="主账号", kind="OWNER")
        self.client = Client()
        self.client.post("/api/v1/admin/auth/login", data='{"loginName":"audit-owner","password":"Synthetic password 2026!"}',
                         content_type="application/json")

    def test_filters_pagination_and_private_redaction(self):
        for index in range(3):
            AuditLog.objects.create(actor=self.owner, action_code="catalog.update", object_type="product",
                object_id=str(index), result="SUCCESS", request_id=uuid.uuid4(),
                before={"status": "DRAFT", "password": "secret-123", "nested": {"phone": "13800138000"}},
                after={"status": "ON_SALE", "revision": 2, "freeText": "private detail", "unknown": 13800138000})
        result = self.client.get("/api/v1/admin/audit-logs", {"actionCode": "catalog.update", "limit": "2"})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIn("private", result["Cache-Control"])
        self.assertIn("no-store", result["Cache-Control"])
        page = result.json()["data"]
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(page["nextCursor"])
        self.assertNotIn("secret-123", result.content.decode())
        self.assertNotIn("13800138000", result.content.decode())
        self.assertNotIn("private detail", result.content.decode())
        self.assertNotIn("13800138000", result.content.decode())
        self.assertEqual(page["items"][0]["after"]["revision"], 2)
        second = self.client.get("/api/v1/admin/audit-logs", {"actionCode": "catalog.update", "limit": "2",
                                                          "cursor": page["nextCursor"]})
        self.assertEqual(len(second.json()["data"]["items"]), 1)
        self.assertEqual(second.json()["data"]["nextCursor"], None)
        mismatch = self.client.get("/api/v1/admin/audit-logs", {"actionCode": "other", "cursor": page["nextCursor"]})
        self.assertEqual(mismatch.status_code, 400)
        tampered = self.client.get("/api/v1/admin/audit-logs", {"actionCode": "catalog.update",
                                                               "cursor": page["nextCursor"] + "x"})
        self.assertEqual(tampered.status_code, 400)

    def test_denied_and_date_validation(self):
        staff = AdminAccount.objects.create_user("audit-staff", "Synthetic password 2026!",
            display_name="员工", kind="STAFF")
        group = PermissionGroup.objects.create(code="report-only", name="仅经营")
        GroupPermission.objects.create(group=group, code="business.report.read")
        staff.permission_groups.add(group)
        client = Client()
        client.post("/api/v1/admin/auth/login", data='{"loginName":"audit-staff","password":"Synthetic password 2026!"}',
                    content_type="application/json")
        self.assertEqual(client.get("/api/v1/admin/audit-logs").status_code, 403)
        too_old = (timezone.localdate() - timedelta(days=91)).isoformat()
        self.assertEqual(self.client.get("/api/v1/admin/audit-logs", {"from": too_old}).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/admin/audit-logs?result=SUCCESS&result=FAILED").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/admin/audit-logs", {"objectId": "line\nother"}).status_code, 400)

    def test_default_window_stays_on_shanghai_calendar_when_active_zone_differs(self):
        with patch("accounts.read_window.timezone.now", return_value=datetime(2026, 9, 27, 17, tzinfo=utc_timezone.utc)):
            with timezone.override("UTC"):
                first, last, start, end = read_window({})
        self.assertEqual(last.isoformat(), "2026-09-28")
        self.assertEqual(first.isoformat(), "2026-07-01")
        self.assertEqual(start.utcoffset(), timedelta(hours=8))
        self.assertEqual(end.utcoffset(), timedelta(hours=8))

    def test_unknown_enum_and_nested_payload_never_leave_raw_values(self):
        safe = _safe_json({"status": "unexpected-private-value", "unknown": 13800138000,
                           "details": [{"password": "hidden"}], "revision": 2})
        self.assertEqual(safe["status"], "[已脱敏]")
        self.assertEqual(safe["unknown"], "[已脱敏]")
        self.assertEqual(safe["details"], "[已脱敏]")
        self.assertEqual(safe["revision"], 2)

    def test_audit_read_is_limited_per_account(self):
        for _ in range(60):
            self.assertEqual(self.client.get("/api/v1/admin/audit-logs").status_code, 200)
        denied = self.client.get("/api/v1/admin/audit-logs")
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(denied.json()["error"]["code"], "RATE_LIMITED")
        self.assertGreater(int(denied["Retry-After"]), 0)
