import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AdminAccount, AuditLog, PermissionGroup


OWNER_PASSWORD = "Safe owner passphrase 2026!"
STAFF_PASSWORD = "Safe staff passphrase 2026!"


class AccountFlowTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user("owner", OWNER_PASSWORD,
            display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = Client(enforce_csrf_checks=True)
        self.client.get("/api/v1/admin/auth/csrf")

    def send(self, client, verb, path, payload=None, token=None):
        csrf = client.cookies["csrftoken"].value
        headers = {"HTTP_X_CSRFTOKEN": csrf}
        if token:
            headers["HTTP_X_ACTION_CONFIRMATION"] = token
        return getattr(client, verb)(path, data=json.dumps(payload or {}),
                                     content_type="application/json", **headers)

    def login(self, client, name, password):
        return self.send(client, "post", "/api/v1/admin/auth/login",
                         {"loginName": name, "password": password})

    def confirm(self, client, action, password=OWNER_PASSWORD, object_id="", revision=0):
        result = self.send(client, "post", "/api/v1/admin/auth/confirm",
            {"action": action, "password": password, "objectId": str(object_id), "revision": revision})
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]["confirmationToken"]

    def create_staff(self, group):
        token = self.confirm(self.client, "account.create")
        result = self.send(self.client, "post", "/api/v1/admin/accounts",
            {"loginName": "staff", "displayName": "受限员工", "password": STAFF_PASSWORD,
             "groupIds": [str(group.id)]}, token)
        self.assertEqual(result.status_code, 201, result.content)
        return AdminAccount.objects.get(login_name="staff")

    def test_six_character_password_is_valid_for_staff_creation(self):
        self.login(self.client, "owner", OWNER_PASSWORD)
        group = PermissionGroup.objects.create(code="six-password", name="密码测试")
        token = self.confirm(self.client, "account.create")
        result = self.send(self.client, "post", "/api/v1/admin/accounts",
            {"loginName": "short-password-staff", "displayName": "新员工", "password": "123456",
             "groupIds": [str(group.id)]}, token)
        self.assertEqual(result.status_code, 201, result.content)
        self.assertTrue(AdminAccount.objects.get(login_name="short-password-staff").check_password("123456"))

    def test_interactive_owner_is_unique_and_database_enforces_it(self):
        with patch("builtins.input", side_effect=["another", "另一个"]), patch(
            "getpass.getpass", side_effect=[OWNER_PASSWORD, OWNER_PASSWORD]
        ):
            with self.assertRaises(CommandError):
                call_command("create_owner")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AdminAccount.objects.create_user("another", OWNER_PASSWORD,
                display_name="另一个", kind=AdminAccount.Kind.OWNER)

    def test_interactive_owner_creation_on_empty_database(self):
        self.owner.delete()
        with patch("builtins.input", side_effect=["first-owner", "初始主账号"]), patch(
            "getpass.getpass", side_effect=[OWNER_PASSWORD, OWNER_PASSWORD]
        ):
            call_command("create_owner")
        created = AdminAccount.objects.get(kind=AdminAccount.Kind.OWNER)
        self.assertTrue(created.check_password(OWNER_PASSWORD))
        self.assertTrue(AuditLog.objects.filter(action_code="account.bootstrap", actor=created).exists())

    def test_restricted_staff_allowed_group_read_but_denied_account_write(self):
        self.login(self.client, "owner", OWNER_PASSWORD)
        group = PermissionGroup.objects.create(code="group_reader", name="权限组只读")
        from accounts.models import GroupPermission
        GroupPermission.objects.create(group=group, code="permission.read")
        self.create_staff(group)
        staff_client = Client(enforce_csrf_checks=True)
        staff_client.get("/api/v1/admin/auth/csrf")
        self.assertEqual(self.login(staff_client, "staff", STAFF_PASSWORD).status_code, 200)
        self.assertEqual(staff_client.get("/api/v1/admin/permission-groups").status_code, 200)
        self.assertEqual(self.send(staff_client, "post", "/api/v1/admin/accounts", {}).status_code, 403)

    def test_staff_cannot_modify_own_permission_group(self):
        from accounts.models import GroupPermission
        self.login(self.client, "owner", OWNER_PASSWORD)
        group = PermissionGroup.objects.create(code="group_manager", name="权限组管理员")
        GroupPermission.objects.create(group=group, code="permission.manage")
        self.create_staff(group)
        staff_client = Client(enforce_csrf_checks=True)
        staff_client.get("/api/v1/admin/auth/csrf")
        self.login(staff_client, "staff", STAFF_PASSWORD)
        token = self.confirm(staff_client, "group.update", STAFF_PASSWORD, group.id, group.revision)
        result = self.send(staff_client, "patch", f"/api/v1/admin/permission-groups/{group.id}",
            {"permissionCodes": ["permission.manage", "account.manage"],
             "expectedRevision": group.revision}, token)
        self.assertEqual(result.status_code, 403)
        self.assertFalse(GroupPermission.objects.filter(group=group, code="account.manage").exists())

    def test_login_limited_staff_denial_and_disable_invalidates_session(self):
        self.assertEqual(self.login(self.client, "owner", OWNER_PASSWORD).status_code, 200)
        group = PermissionGroup.objects.get(code="catalog_operator")
        staff = self.create_staff(group)
        staff_client = Client(enforce_csrf_checks=True)
        staff_client.get("/api/v1/admin/auth/csrf")
        login_result = self.login(staff_client, "staff", STAFF_PASSWORD)
        self.assertEqual(login_result.status_code, 200)
        self.assertIn("catalog.read", login_result.json()["data"]["permissionCodes"])
        self.assertEqual(staff_client.get("/api/v1/admin/me").status_code, 200)
        self.assertEqual(staff_client.get("/api/v1/admin/accounts").status_code, 403)
        self.assertEqual(staff_client.get("/api/v1/admin/permission-groups").status_code, 403)
        self.assertEqual(self.send(staff_client, "post", "/api/v1/admin/accounts", {}).status_code, 403)
        token = self.confirm(self.client, "account.disable", object_id=staff.id, revision=staff.revision)
        result = self.send(self.client, "patch", f"/api/v1/admin/accounts/{staff.id}",
            {"enabled": False, "expectedRevision": staff.revision}, token)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(staff_client.get("/api/v1/admin/me").status_code, 401)
        self.assertEqual(self.login(staff_client, "staff", STAFF_PASSWORD).status_code, 401)
        self.assertTrue(AuditLog.objects.filter(action_code="account.disable", result="SUCCESS").exists())

    def test_group_change_revokes_old_session(self):
        self.login(self.client, "owner", OWNER_PASSWORD)
        group = PermissionGroup.objects.get(code="catalog_operator")
        staff = self.create_staff(group)
        staff_client = Client(enforce_csrf_checks=True)
        staff_client.get("/api/v1/admin/auth/csrf")
        self.assertEqual(self.login(staff_client, "staff", STAFF_PASSWORD).status_code, 200)
        token = self.confirm(self.client, "group.update", object_id=group.id, revision=group.revision)
        result = self.send(self.client, "patch", f"/api/v1/admin/permission-groups/{group.id}",
            {"permissionCodes": ["catalog.read"], "expectedRevision": group.revision}, token)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(staff_client.get("/api/v1/admin/me").status_code, 401)
        staff.refresh_from_db()
        self.assertEqual(staff.auth_version, 2)

    def test_confirmation_is_bound_and_single_use(self):
        self.login(self.client, "owner", OWNER_PASSWORD)
        group = PermissionGroup.objects.get(code="catalog_operator")
        token = self.confirm(self.client, "account.create")
        body = {"loginName": "staff", "displayName": "受限员工", "password": STAFF_PASSWORD,
                "groupIds": [str(group.id)]}
        self.assertEqual(self.send(self.client, "post", "/api/v1/admin/accounts", body, token).status_code, 201)
        body["loginName"] = "staff2"
        self.assertEqual(self.send(self.client, "post", "/api/v1/admin/accounts", body, token).status_code, 403)

    def test_confirmation_is_bound_to_target_and_stored_as_hash(self):
        from accounts.models import ActionConfirmation
        self.login(self.client, "owner", OWNER_PASSWORD)
        group = PermissionGroup.objects.get(code="catalog_operator")
        staff = self.create_staff(group)
        token = self.confirm(self.client, "account.disable", object_id=uuid.UUID(int=0),
                             revision=staff.revision)
        confirmation = ActionConfirmation.objects.get(action="account.disable")
        self.assertNotEqual(confirmation.token_hash, token)
        result = self.send(self.client, "patch", f"/api/v1/admin/accounts/{staff.id}",
            {"enabled": False, "expectedRevision": staff.revision}, token)
        self.assertEqual(result.status_code, 403)
        confirmation.refresh_from_db()
        self.assertIsNotNone(confirmation.consumed_at)
        staff.refresh_from_db()
        self.assertTrue(staff.enabled)

    def test_csrf_and_lockout_and_idle_expiry(self):
        without_csrf = self.client.post("/api/v1/admin/auth/login", data=json.dumps(
            {"loginName": "owner", "password": OWNER_PASSWORD}), content_type="application/json")
        self.assertEqual(without_csrf.status_code, 403)
        for _ in range(5):
            self.assertEqual(self.login(self.client, "owner", "bad password").status_code, 401)
        self.assertEqual(self.login(self.client, "owner", OWNER_PASSWORD).status_code, 429)
        self.owner.locked_until = timezone.now() - timedelta(seconds=1)
        self.owner.save(update_fields=["locked_until"])
        self.assertEqual(self.login(self.client, "owner", "bad password").status_code, 401)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.failed_count, 1)
        self.assertEqual(self.login(self.client, "owner", OWNER_PASSWORD).status_code, 200)
        session = self.client.session
        session["admin_last_active"] = 0
        session.save()
        self.assertEqual(self.client.get("/api/v1/admin/me").status_code, 401)

    def test_vite_origin_is_allowed_with_valid_csrf(self):
        result = self.client.post("/api/v1/admin/auth/login",
            data=json.dumps({"loginName": "owner", "password": OWNER_PASSWORD}),
            content_type="application/json", HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
            HTTP_ORIGIN="http://127.0.0.1:5173")
        self.assertEqual(result.status_code, 200, result.content)

    def test_source_rate_limit_applies_to_unknown_logins(self):
        for index in range(10):
            result = self.login(self.client, f"unknown-{index}", "bad password")
            self.assertEqual(result.status_code, 401)
        blocked = self.login(self.client, "owner", OWNER_PASSWORD)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["error"]["code"], "RATE_LIMITED")

    def test_unknown_login_runs_dummy_password_check(self):
        with patch("accounts.views.check_password", return_value=False) as dummy:
            result = self.login(self.client, "unknown", "bad password")
        self.assertEqual(result.status_code, 401)
        dummy.assert_called_once()

    def test_confirmation_password_attempts_are_limited(self):
        self.login(self.client, "owner", OWNER_PASSWORD)
        for _ in range(5):
            result = self.send(self.client, "post", "/api/v1/admin/auth/confirm",
                {"action": "account.create", "password": "wrong password"})
            self.assertEqual(result.status_code, 403)
        result = self.send(self.client, "post", "/api/v1/admin/auth/confirm",
            {"action": "account.create", "password": OWNER_PASSWORD})
        self.assertEqual(result.status_code, 429)
        self.owner.refresh_from_db()
        self.owner.locked_until = timezone.now() - timedelta(seconds=1)
        self.owner.save(update_fields=["locked_until"])
        token = self.confirm(self.client, "account.create")
        self.assertTrue(token)

    def test_revision_boolean_is_rejected(self):
        self.login(self.client, "owner", OWNER_PASSWORD)
        result = self.send(self.client, "post", "/api/v1/admin/auth/confirm",
            {"action": "account.disable", "password": OWNER_PASSWORD,
             "objectId": "any", "revision": True})
        self.assertEqual(result.status_code, 400)

    def test_audit_never_contains_password_and_logout_ends_session(self):
        self.assertEqual(self.login(self.client, "owner", OWNER_PASSWORD).status_code, 200)
        self.assertEqual(self.send(self.client, "post", "/api/v1/admin/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/admin/me").status_code, 401)
        serialized = " ".join(json.dumps([log.before, log.after]) for log in AuditLog.objects.all())
        self.assertNotIn(OWNER_PASSWORD, serialized)
