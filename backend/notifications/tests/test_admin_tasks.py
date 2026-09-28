"""Admin task reads and explicitly fenced reservation recovery."""

import json
import secrets
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccountGroup, AdminAccount, AuditLog, GroupPermission, PermissionGroup
from catalog.models import MemberGrade
from customers.models import Member
from notifications.models import MessageAttempt, MessageTask
from notifications.service import recover_expired_claims


PASSWORD = secrets.token_urlsafe(24)
PATH = "/api/v1/admin/subscription-message-tasks"


class AdminMessageTaskApiTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "message-task-owner", PASSWORD, display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = self._login(self.owner)
        grade = MemberGrade.objects.create(code="message-task-grade", name="消息会员", rank=97)
        self.member = Member.objects.create(wechat_app_id="secret-app", wechat_openid="secret-openid", grade=grade)
        self.now = timezone.now()

    def _login(self, account):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self._post(client, "/api/v1/admin/auth/login", {
            "loginName": account.login_name, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def _post(self, client, path, body, *, confirmation="", csrf=True):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value} if csrf else {}
        if confirmation:
            headers["HTTP_X_ACTION_CONFIRMATION"] = confirmation
        return client.post(path, data=json.dumps(body), content_type="application/json", **headers)

    def _staff(self, *codes):
        suffix = uuid.uuid4().hex[:8]
        group = PermissionGroup.objects.create(code=f"message-task-{suffix}", name=f"消息员工{suffix}")
        for code in codes:
            GroupPermission.objects.create(group=group, code=code)
        account = AdminAccount.objects.create_user(f"staff-{uuid.uuid4().hex[:8]}", PASSWORD, display_name="员工")
        AccountGroup.objects.create(account=account, group=group)
        return self._login(account)

    def _task(self, *, status="BLOCKED", event="ORDER_PAID", created_at=None):
        leased = status in {"RESERVED", "CLAIMED"}
        task = MessageTask.objects.create(
            event_type=event, source_id=uuid.uuid4(), member=self.member, occurred_at=self.now,
            identity_digest="a" * 64, template_id_snapshot="secret-template", status=status,
            reason_code="NO_AUTHORIZATION" if status == "BLOCKED" else "",
            lease_token=uuid.uuid4() if leased else None,
            lease_until=self.now - timedelta(minutes=1) if leased else None)
        if created_at:
            MessageTask.objects.filter(pk=task.pk).update(created_at=created_at)
            task.refresh_from_db()
        return task

    def _confirm(self, client, task):
        result = self._post(client, "/api/v1/admin/auth/confirm", {
            "action": "notification.task.recover_reservation", "objectId": str(task.pk),
            "revision": task.attempt_count, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]["confirmationToken"]

    def _recover(self, client, task, *, token=None, body=None):
        return self._post(client, f"{PATH}/{task.pk}/recover-reservation",
                          body or {"expectedUpdatedAt": task.updated_at.isoformat(),
                                   "expectedAttemptCount": task.attempt_count},
                          confirmation=token or "")

    def test_list_filters_paginates_and_detail_redacts_private_fields(self):
        first = self._task(event="ORDER_PAID", created_at=self.now - timedelta(days=2))
        second = self._task(event="ORDER_PAID", created_at=self.now - timedelta(days=1))
        self._task(event="ORDER_SHIPPED")
        path = f"{PATH}?eventType=ORDER_PAID&status=BLOCKED&limit=1"
        page1 = self.client.get(path)
        self.assertEqual(page1.status_code, 200, page1.content)
        self.assertIn("no-store", page1["Cache-Control"])
        self.assertEqual(page1.json()["data"]["items"][0]["taskId"], str(second.pk))
        cursor = page1.json()["data"]["nextCursor"]
        self.assertTrue(cursor)
        page2 = self.client.get(path + "&cursor=" + cursor)
        self.assertEqual(page2.status_code, 200, page2.content)
        self.assertEqual(page2.json()["data"]["items"][0]["taskId"], str(first.pk))
        self.assertIsNone(page2.json()["data"]["nextCursor"])
        detail = self.client.get(f"{PATH}/{first.pk}")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertIn("no-store", detail["Cache-Control"])
        self.assertEqual(detail.json()["data"]["attempts"], [])
        for value in ("secret-app", "secret-openid", "secret-template", "a" * 64,
                      str(self.member.pk), str(first.source_id), "leaseToken"):
            self.assertNotIn(value, page1.content.decode() + detail.content.decode())

    def test_read_validation_and_permissions(self):
        self._task()
        self.assertEqual(Client().get(PATH).status_code, 401)
        self.assertEqual(self._staff().get(PATH).status_code, 403)
        self.assertEqual(self._staff("notification.read").get(PATH).status_code, 200)
        for suffix in ("?status=INVALID", "?eventType=INVALID", "?limit=101", "?limit=0",
                       "?createdFrom=not-a-date", "?createdFrom=2026-01-01T00:00:00",
                       "?cursor=bad"):
            self.assertEqual(self.client.get(PATH + suffix).status_code, 400, suffix)
        self.assertEqual(self.client.get(f"{PATH}/{uuid.uuid4()}").status_code, 404)
        old = self._task(created_at=self.now - timedelta(days=100))
        self.assertNotIn(str(old.pk), self.client.get(PATH).content.decode())
        self.assertEqual(self.client.get(f"{PATH}?createdFrom=2026-01-01&createdTo=2026-04-10").status_code, 400)

    def test_date_bounds_cursor_scope_and_attempt_summary(self):
        older = self._task(created_at=self.now - timedelta(days=3))
        current = self._task(status="UNKNOWN", created_at=self.now)
        MessageAttempt.objects.create(task=current, ordinal=1, lease_token=uuid.uuid4(),
                                      started_at=self.now - timedelta(minutes=2),
                                      finished_at=self.now - timedelta(minutes=1),
                                      outcome="UNKNOWN", failure_code="LEASE_EXPIRED")
        today = timezone.localtime(self.now).date().isoformat()
        filtered = self.client.get(f"{PATH}?createdFrom={today}&createdTo={today}")
        self.assertEqual(filtered.status_code, 200, filtered.content)
        self.assertEqual([row["taskId"] for row in filtered.json()["data"]["items"]], [str(current.pk)])
        paged = self.client.get(f"{PATH}?limit=1")
        cursor = paged.json()["data"]["nextCursor"]
        self.assertEqual(paged.json()["data"]["items"][0]["taskId"], str(current.pk))
        self.assertEqual(self.client.get(f"{PATH}?limit=1&status=UNKNOWN&cursor={cursor}").status_code, 400)
        self.assertEqual(self.client.get(f"{PATH}?limit=1&cursor={cursor}").json()["data"]["items"][0]["taskId"],
                         str(older.pk))
        detail = self.client.get(f"{PATH}/{current.pk}").json()["data"]
        self.assertEqual(detail["attempts"][0]["outcome"], "UNKNOWN")
        self.assertEqual(detail["attempts"][0]["failureCode"], "LEASE_EXPIRED")
        self.assertFalse(detail["recoverable"])
        self.assertNotIn("leaseToken", json.dumps(detail))

    def test_expired_reservation_requires_confirm_and_rechecks_stale_state(self):
        task = self._task(status="RESERVED")
        path = f"{PATH}/{task.pk}/recover-reservation"
        self.assertEqual(self.client.get(f"{PATH}/{task.pk}").json()["data"]["recoverable"], True)
        self.assertEqual(self._recover(self.client, task).status_code, 403)
        self.assertEqual(self._recover(self.client, task, token=self._confirm(self.client, task),
                                       body={"expectedUpdatedAt": task.updated_at.isoformat(),
                                             "expectedAttemptCount": 1}).status_code, 409)
        task.refresh_from_db()
        result = self._recover(self.client, task, token=self._confirm(self.client, task))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIn("no-store", result["Cache-Control"])
        self.assertEqual(result.json()["data"]["status"], "READY")
        task.refresh_from_db()
        self.assertIsNone(task.lease_token)
        self.assertEqual(task.attempt_count, 0)
        self.assertEqual(self._recover(self.client, task, token=self._confirm(self.client, task)).status_code, 409)
        self.assertEqual(AuditLog.objects.filter(action_code="notification.task.recover_reservation",
                                                 object_id=str(task.pk), result="SUCCESS").count(), 1)

    def test_recovery_refuses_inflight_unknown_unexpired_and_anomalous_attempt(self):
        for status in ("CLAIMED", "UNKNOWN", "READY"):
            task = self._task(status=status)
            self.assertEqual(self._recover(self.client, task, token=self._confirm(self.client, task)).status_code, 409)
            task.refresh_from_db()
            self.assertEqual(task.status, status)
        task = self._task(status="RESERVED")
        task.lease_until = self.now + timedelta(minutes=1)
        task.save(update_fields=["lease_until", "updated_at"])
        self.assertEqual(self._recover(self.client, task, token=self._confirm(self.client, task)).status_code, 409)
        task.lease_until = self.now - timedelta(minutes=1)
        task.save(update_fields=["lease_until", "updated_at"])
        MessageAttempt.objects.create(task=task, ordinal=1, lease_token=task.lease_token,
                                      started_at=self.now, outcome="IN_FLIGHT")
        self.assertEqual(self._recover(self.client, task, token=self._confirm(self.client, task)).status_code, 409)
        task.refresh_from_db()
        self.assertEqual(task.status, "RESERVED")
        self.assertGreaterEqual(recover_expired_claims(now=self.now + timedelta(minutes=2), limit=10), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "UNKNOWN")
        self.assertEqual(MessageAttempt.objects.get(task=task).outcome, "UNKNOWN")

    def test_expired_retry_reservation_with_prior_completed_attempt_is_safe(self):
        task = self._task(status="RESERVED")
        prior_token = uuid.uuid4()
        MessageAttempt.objects.create(task=task, ordinal=1, lease_token=prior_token,
                                      started_at=self.now - timedelta(minutes=10),
                                      finished_at=self.now - timedelta(minutes=9),
                                      outcome="RETRYABLE", failure_code="TEMPORARY_FAILURE")
        task.attempt_count = 1
        task.save(update_fields=["attempt_count", "updated_at"])
        detail = self.client.get(f"{PATH}/{task.pk}").json()["data"]
        self.assertTrue(detail["recoverable"])
        self.assertEqual(detail["attemptCount"], 1)
        result = self._recover(self.client, task, token=self._confirm(self.client, task))
        self.assertEqual(result.status_code, 200, result.content)
        task.refresh_from_db()
        self.assertEqual((task.status, task.attempt_count), ("READY", 1))
        self.assertEqual(MessageAttempt.objects.get(task=task).outcome, "RETRYABLE")

        batch_task = self._task(status="RESERVED")
        MessageAttempt.objects.create(task=batch_task, ordinal=1, lease_token=uuid.uuid4(),
                                      started_at=self.now - timedelta(minutes=10),
                                      finished_at=self.now - timedelta(minutes=9),
                                      outcome="RETRYABLE", failure_code="TEMPORARY_FAILURE")
        batch_task.attempt_count = 1
        batch_task.save(update_fields=["attempt_count", "updated_at"])
        self.assertGreaterEqual(recover_expired_claims(now=self.now + timedelta(minutes=2), limit=10), 1)
        batch_task.refresh_from_db()
        self.assertEqual((batch_task.status, batch_task.attempt_count), ("READY", 1))

    def test_recovery_permission_csrf_and_rate_limit(self):
        task = self._task(status="RESERVED")
        reader = self._staff("notification.read")
        writer = self._staff("notification.recover")
        reader_writer = self._staff("notification.read", "notification.recover")
        self.assertEqual(self._recover(reader, task).status_code, 403)
        self.assertEqual(reader.get(f"{PATH}/{task.pk}").status_code, 200)
        self.assertEqual(writer.get(PATH).status_code, 403)
        self.assertEqual(self._post(writer, "/api/v1/admin/auth/confirm", {
            "action": "notification.task.recover_reservation", "objectId": str(task.pk),
            "revision": task.attempt_count, "password": PASSWORD}).status_code, 403)
        self.assertEqual(self._recover(writer, task).status_code, 403)
        self.assertEqual(self._recover(reader_writer, task,
                                       token=self._confirm(reader_writer, task)).status_code, 200)
        next_task = self._task(status="RESERVED")
        for _ in range(10):
            AuditLog.objects.create(actor=self.owner, action_code="notification.task.recover_reservation",
                                    object_type="subscription_message_task", object_id=str(uuid.uuid4()),
                                    request_id=uuid.uuid4(), result="SUCCESS")
        limited = self._recover(self.client, next_task, token=self._confirm(self.client, next_task))
        self.assertEqual(limited.status_code, 429, limited.content)
        self.assertGreater(int(limited["Retry-After"]), 0)
        next_task.refresh_from_db()
        self.assertEqual(next_task.status, "RESERVED")
        self.assertEqual(self._post(self.client, f"{PATH}/{next_task.pk}/recover-reservation", {},
                                    csrf=False).status_code, 403)
