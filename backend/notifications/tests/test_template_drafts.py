"""E2.1 draft bindings remain separate from live subscription send policy."""

import json
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AdminAccount, AuditLog, GroupPermission, PermissionGroup, AccountGroup
from notifications.models import SubscriptionTemplate, SubscriptionTemplateDraft


PASSWORD = "Safe subscription admin passphrase 2026!"
LIST_PATH = "/api/v1/admin/subscription-templates"
PUBLIC_PATH = "/api/v1/app/subscription-messages/availability"
EVENTS = ("ORDER_PAID", "ORDER_SHIPPED", "REFUND_SUCCEEDED")


class TemplateDraftApiTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "subscription-owner", PASSWORD, display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = self._login(self.owner)

    def _login(self, account):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self._send(client, "post", "/api/v1/admin/auth/login", {
            "loginName": account.login_name, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def _send(self, client, method, path, body, *, csrf=True):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value} if csrf else {}
        return getattr(client, method)(path, data=json.dumps(body), content_type="application/json", **headers)

    def _staff(self, *codes):
        group = PermissionGroup.objects.create(code="subscription-staff", name="订阅配置员工")
        for code in codes:
            GroupPermission.objects.create(group=group, code=code)
        account = AdminAccount.objects.create_user("subscription-staff", PASSWORD, display_name="员工")
        AccountGroup.objects.create(account=account, group=group)
        return self._login(account)

    def test_owner_reads_fixed_unbound_events_and_public_is_unavailable(self):
        result = self.client.get(LIST_PATH)
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertFalse(data["sendingAvailable"])
        self.assertEqual(tuple(item["eventType"] for item in data["events"]), EVENTS)
        for item in data["events"]:
            self.assertEqual(item, {"eventType": item["eventType"], "revision": 0,
                                    "draftAppId": "", "draftTemplateId": "",
                                    "status": "UNBOUND", "enabled": False})
        public = Client().get(PUBLIC_PATH)
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public.json()["data"], {
            "available": False, "reasonCode": "PLATFORM_NOT_VERIFIED",
            "events": [{"eventType": event, "available": False} for event in EVENTS]})
        self.assertNotIn("templateId", public.content.decode())

    def test_permissions_and_csrf_protect_drafts(self):
        self.assertEqual(Client().get(LIST_PATH).status_code, 401)
        unauthenticated = Client(enforce_csrf_checks=True)
        self.assertEqual(self._send(unauthenticated, "put", f"{LIST_PATH}/ORDER_PAID", {
            "expectedRevision": 0, "draftAppId": "", "draftTemplateId": ""}, csrf=False).status_code, 403)
        reader = self._staff("notification.read")
        self.assertEqual(reader.get(LIST_PATH).status_code, 200)
        self.assertEqual(self._send(reader, "put", f"{LIST_PATH}/ORDER_PAID", {
            "expectedRevision": 0, "draftAppId": "", "draftTemplateId": ""}).status_code, 403)
        self.assertEqual(self._send(self.client, "put", f"{LIST_PATH}/ORDER_PAID", {
            "expectedRevision": 0, "draftAppId": "", "draftTemplateId": ""}, csrf=False).status_code, 403)
        self.assertEqual(SubscriptionTemplateDraft.objects.count(), 3)

    def test_save_incomplete_then_unverified_draft_without_enabling_binding(self):
        path = f"{LIST_PATH}/ORDER_PAID"
        first = self._send(self.client, "put", path, {
            "expectedRevision": 0, "draftAppId": "wxDraftApp", "draftTemplateId": ""})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["data"]["status"], "INCOMPLETE")
        self.assertEqual(first.json()["data"]["revision"], 1)
        second = self._send(self.client, "put", path, {
            "expectedRevision": 1, "draftAppId": "wxDraftApp", "draftTemplateId": "draft-template-id"})
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()["data"]["status"], "DRAFT_UNVERIFIED")
        self.assertEqual(second.json()["data"]["revision"], 2)
        self.assertFalse(second.json()["data"]["enabled"])
        self.assertFalse(SubscriptionTemplate.objects.exists())
        self.assertEqual(SubscriptionTemplateDraft.objects.get(event_type="ORDER_PAID").revision, 2)
        audits = AuditLog.objects.filter(action_code="notification.template_draft.update")
        self.assertEqual(audits.count(), 2)
        self.assertTrue(all(item.result == "SUCCESS" for item in audits))

    def test_stale_revision_and_replay_preserve_current_draft(self):
        path = f"{LIST_PATH}/ORDER_SHIPPED"
        original = {"expectedRevision": 0, "draftAppId": "wxAppOne", "draftTemplateId": "template-one"}
        self.assertEqual(self._send(self.client, "put", path, original).status_code, 200)
        stale = self._send(self.client, "put", path, original)
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")
        row = SubscriptionTemplateDraft.objects.get(event_type="ORDER_SHIPPED")
        self.assertEqual((row.revision, row.draft_app_id, row.draft_template_id),
                         (1, "wxAppOne", "template-one"))

    def test_invalid_contract_and_unknown_event_are_rejected(self):
        path = f"{LIST_PATH}/REFUND_SUCCEEDED"
        for body in (
            {"expectedRevision": True, "draftAppId": "", "draftTemplateId": ""},
            {"expectedRevision": 0, "draftAppId": "a" * 65, "draftTemplateId": ""},
            {"expectedRevision": 0, "draftAppId": "with space", "draftTemplateId": ""},
            {"expectedRevision": 0, "draftAppId": "", "draftTemplateId": "", "enabled": True},
        ):
            self.assertEqual(self._send(self.client, "put", path, body).status_code, 400)
        self.assertEqual(self._send(self.client, "put", f"{LIST_PATH}/UNKNOWN", {
            "expectedRevision": 0, "draftAppId": "", "draftTemplateId": ""}).status_code, 404)
        self.assertEqual(SubscriptionTemplateDraft.objects.count(), 3)

    def test_public_availability_stays_closed_even_with_synthetic_binding(self):
        SubscriptionTemplate.objects.create(event_type="ORDER_PAID", wechat_app_id="wx-test",
                                            template_id="synthetic-id", enabled=True)
        self._send(self.client, "put", f"{LIST_PATH}/ORDER_PAID", {
            "expectedRevision": 0, "draftAppId": "wxAppOne", "draftTemplateId": "template-one"})
        body = Client().get(PUBLIC_PATH).json()["data"]
        self.assertFalse(body["available"])
        self.assertTrue(all(not item["available"] for item in body["events"]))
        self.assertNotIn("synthetic-id", json.dumps(body))
        self.assertNotIn("template-one", json.dumps(body))

    def test_write_limit_rejects_audit_growth_without_changing_draft(self):
        for _ in range(30):
            AuditLog.objects.create(actor=self.owner, action_code="notification.template_draft.update",
                                    object_type="subscription_template_draft", object_id="ORDER_PAID",
                                    request_id=uuid.uuid4(), result="SUCCESS")
        response = self._send(self.client, "put", f"{LIST_PATH}/ORDER_PAID", {
            "expectedRevision": 0, "draftAppId": "wxDraft", "draftTemplateId": ""})
        self.assertEqual(response.status_code, 429, response.content)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertEqual(SubscriptionTemplateDraft.objects.get(event_type="ORDER_PAID").revision, 0)
        self.assertEqual(SubscriptionTemplate.objects.count(), 0)
        AuditLog.objects.filter(actor=self.owner, action_code="notification.template_draft.update").update(
            occurred_at=timezone.now() - timedelta(hours=2))
        allowed = self._send(self.client, "put", f"{LIST_PATH}/ORDER_PAID", {
            "expectedRevision": 0, "draftAppId": "wxDraft", "draftTemplateId": ""})
        self.assertEqual(allowed.status_code, 200, allowed.content)
