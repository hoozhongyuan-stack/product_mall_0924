import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccountGroup, ActionConfirmation, AdminAccount, AuditLog, GroupPermission, PermissionGroup


OLD_PASSWORD = "Synthetic old passphrase 2026!"
NEW_PASSWORD = "Synthetic replacement passphrase 2027!"


class PasswordTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user("owner", OLD_PASSWORD, display_name="主账号", kind="OWNER")
        self.staff = AdminAccount.objects.create_user("staff", OLD_PASSWORD, display_name="子账号")
        self.client = self.signed_in(self.owner)

    def signed_in(self, account):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.send(client, "/auth/login", {"loginName": account.login_name, "password": OLD_PASSWORD})
        self.assertEqual(result.status_code, 200)
        return client

    def send(self, client, path, body):
        return client.post("/api/v1/admin" + path, data=json.dumps(body), content_type="application/json",
                           HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    def pending(self, actor, client):
        return ActionConfirmation.objects.create(actor=actor, session_key=client.session.session_key,
            token_hash=hashlib.sha256(str(actor.pk).encode()).hexdigest(), action="account.create",
            object_id="", revision=0, expires_at=timezone.now() + timedelta(minutes=5))

    def payload(self, **extra):
        return {"currentPassword": OLD_PASSWORD, "newPassword": NEW_PASSWORD, **extra}

    def test_staff_can_change_own_password_without_account_permissions_and_all_sessions_expire(self):
        first, second = self.signed_in(self.staff), self.signed_in(self.staff)
        pending = self.pending(self.staff, first)
        result = self.send(first, "/auth/password", self.payload())
        self.assertEqual(result.status_code, 200, result.content)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(NEW_PASSWORD))
        self.assertEqual((self.staff.revision, self.staff.auth_version), (2, 2))
        pending.refresh_from_db()
        self.assertIsNotNone(pending.consumed_at)
        self.assertEqual(first.get("/api/v1/admin/me").status_code, 401)
        self.assertEqual(second.get("/api/v1/admin/me").status_code, 401)
        self.assertEqual(self.send(first, "/auth/login", {"loginName": "staff", "password": OLD_PASSWORD}).status_code, 401)
        self.assertEqual(self.send(first, "/auth/login", {"loginName": "staff", "password": NEW_PASSWORD}).status_code, 200)

    def test_owner_can_reset_staff_password_but_owner_session_stays_valid(self):
        staff_client = self.signed_in(self.staff)
        pending = self.pending(self.staff, staff_client)
        result = self.send(self.client, f"/accounts/{self.staff.pk}/password", self.payload(expectedRevision=1))
        self.assertEqual(result.status_code, 200, result.content)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(NEW_PASSWORD))
        self.assertEqual((self.staff.revision, self.staff.auth_version), (2, 2))
        self.assertEqual(staff_client.get("/api/v1/admin/me").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/me").status_code, 200)
        pending.refresh_from_db()
        self.assertIsNotNone(pending.consumed_at)

    def test_staff_with_account_management_and_reset_permission_cannot_reset_anyone(self):
        group = PermissionGroup.objects.create(code="password-test", name="测试账号管理")
        GroupPermission.objects.bulk_create([GroupPermission(group=group, code=code)
            for code in ["account.manage", "account.reset_credentials"]])
        AccountGroup.objects.create(account=self.staff, group=group)
        staff_client = self.signed_in(self.staff)
        for target in [self.staff, self.owner]:
            result = self.send(staff_client, f"/accounts/{target.pk}/password", self.payload(expectedRevision=1))
            self.assertEqual(result.status_code, 403)
            target.refresh_from_db()
            self.assertTrue(target.check_password(OLD_PASSWORD))

    def test_owner_cannot_reset_owner_via_staff_endpoint(self):
        result = self.send(self.client, f"/accounts/{self.owner.pk}/password", self.payload(expectedRevision=1))
        self.assertEqual(result.status_code, 403)

    def test_current_password_required_and_attempts_are_limited(self):
        for index in range(5):
            path = "/auth/password" if index % 2 else f"/accounts/{self.staff.pk}/password"
            result = self.send(self.client, path, self.payload(currentPassword="incorrect", expectedRevision=1))
            self.assertEqual(result.status_code, 403)
        self.assertEqual(self.send(self.client, "/auth/password", self.payload()).status_code, 429)
        self.assertEqual(self.send(self.client, f"/accounts/{self.staff.pk}/password", self.payload(expectedRevision=1)).status_code, 429)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(OLD_PASSWORD))

    def test_weak_password_and_equal_password_are_rejected_without_changes(self):
        for new_password in [None, "123", OLD_PASSWORD]:
            result = self.send(self.client, "/auth/password", self.payload(newPassword=new_password))
            self.assertEqual(result.status_code, 400)
        self.owner.refresh_from_db()
        self.assertEqual((self.owner.auth_version, self.owner.revision), (1, 1))

    def test_revision_conflict_and_invalid_revision_do_not_reset(self):
        for revision, status in [(0, 409), (True, 400), (None, 400)]:
            result = self.send(self.client, f"/accounts/{self.staff.pk}/password", self.payload(expectedRevision=revision))
            self.assertEqual(result.status_code, status)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(OLD_PASSWORD))

    def test_disabled_staff_remains_disabled_after_reset(self):
        self.staff.enabled = False
        self.staff.save(update_fields=["enabled"])
        self.assertEqual(self.send(self.client, f"/accounts/{self.staff.pk}/password", self.payload(expectedRevision=1)).status_code, 200)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.enabled)

    def test_csrf_authentication_and_post_required(self):
        self.assertEqual(self.client.post("/api/v1/admin/auth/password", data=json.dumps(self.payload()), content_type="application/json").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/auth/password").status_code, 405)
        anonymous = Client(enforce_csrf_checks=True)
        anonymous.get("/api/v1/admin/auth/csrf")
        self.assertEqual(self.send(anonymous, "/auth/password", self.payload()).status_code, 401)

    def test_passwords_and_hashes_never_appear_in_audit_or_response(self):
        result = self.send(self.client, f"/accounts/{self.staff.pk}/password", self.payload(expectedRevision=1))
        self.assertEqual(result.status_code, 200)
        self.staff.refresh_from_db()
        serialized = result.content.decode() + json.dumps(list(AuditLog.objects.values("before", "after")))
        for secret in [OLD_PASSWORD, NEW_PASSWORD, self.staff.password]:
            self.assertNotIn(secret, serialized)
        self.assertTrue(AuditLog.objects.filter(action_code="account.password.reset", actor=self.owner, object_id=str(self.staff.pk)).exists())

    def test_session_changed_while_waiting_for_lock_is_rejected(self):
        from accounts.security import require
        def changed_after_require(request):
            actor, bad = require(request)
            AdminAccount.objects.filter(pk=actor.pk).update(auth_version=2)
            return actor, bad
        with patch("accounts.password_views.require", side_effect=changed_after_require):
            result = self.send(self.client, "/auth/password", self.payload())
        self.assertEqual(result.status_code, 401)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(OLD_PASSWORD))

    def test_expired_confirmation_lock_recovers_and_unknown_target_is_not_found(self):
        self.owner.failed_count = 5
        self.owner.locked_until = timezone.now() - timedelta(seconds=1)
        self.owner.save(update_fields=["failed_count", "locked_until"])
        unknown = self.send(self.client, f"/accounts/{uuid.uuid4()}/password", self.payload(expectedRevision=1))
        self.assertEqual(unknown.status_code, 404)
        result = self.send(self.client, f"/accounts/{self.staff.pk}/password", self.payload(expectedRevision=1))
        self.assertEqual(result.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.failed_count, 0)
        self.assertIsNone(self.owner.locked_until)

    def test_successful_resets_have_per_actor_rate_limit(self):
        AuditLog.objects.bulk_create([AuditLog(actor=self.owner, action_code="account.password.reset",
            object_type="admin_account", result="SUCCESS", request_id=uuid.uuid4()) for _ in range(20)])
        result = self.send(self.client, f"/accounts/{self.staff.pk}/password", self.payload(expectedRevision=1))
        self.assertEqual(result.status_code, 429)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(OLD_PASSWORD))

    def test_malformed_json_and_oversized_password_do_not_modify_account(self):
        result = self.client.post("/api/v1/admin/auth/password", data="[]", content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.send(self.client, "/auth/password", self.payload(currentPassword="x" * 1025)).status_code, 400)
        self.assertEqual(self.send(self.client, "/auth/password", self.payload(newPassword="x" * 1025)).status_code, 400)

    def test_password_must_not_match_login_name_or_display_name_for_self_or_owner_reset(self):
        self.staff.login_name = "distinctive-shop-operator-2026"
        self.staff.display_name = "Distinctive Warehouse Operator"
        self.staff.save(update_fields=["login_name", "display_name"])
        staff_client = self.signed_in(self.staff)
        for password in [self.staff.login_name, self.staff.display_name]:
            for client, path, extra in [
                (staff_client, "/auth/password", {}),
                (self.client, f"/accounts/{self.staff.pk}/password", {"expectedRevision": 1}),
            ]:
                with self.subTest(path=path, attribute=password):
                    result = self.send(client, path, self.payload(newPassword=password, **extra))
                    self.assertEqual(result.status_code, 400, result.content)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(OLD_PASSWORD))
        self.assertEqual((self.staff.revision, self.staff.auth_version), (1, 1))
