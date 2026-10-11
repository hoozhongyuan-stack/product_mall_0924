"""Business reauthentication retirement preserves authorization and mutation guards."""
import json
import uuid
from unittest.mock import patch

from django.test import Client, TestCase

from accounts.models import AccountGroup, AdminAccount, AuditLog, GroupPermission, PermissionGroup


class PasswordlessOperationTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user("passwordless-owner", "Login still required 2026!", kind="OWNER")
        self.staff = AdminAccount.objects.create_user("passwordless-staff", "Login still required 2026!")
        self.client = self.login(self.owner)

    def login(self, actor):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.send(client, "post", "/auth/login", {
            "loginName": actor.login_name, "password": "Login still required 2026!"})
        self.assertEqual(result.status_code, 200)
        return client

    def send(self, client, verb, path, body):
        return getattr(client, verb)("/api/v1/admin" + path, data=json.dumps(body),
            content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    def test_create_group_without_confirmation_and_audit(self):
        result = self.send(self.client, "post", "/permission-groups", {
            "code": "passwordless", "name": "免二次密码", "permissionCodes": ["catalog.read"]})
        self.assertEqual(result.status_code, 201, result.content)
        self.assertTrue(AuditLog.objects.filter(actor=self.owner, action_code="group.create").exists())

    def test_legacy_confirmation_endpoint_requires_no_password_and_does_not_lock_login(self):
        for _ in range(6):
            result = self.send(self.client, "post", "/auth/confirm", {"action": "account.create", "password": "wrong"})
            self.assertEqual(result.status_code, 200, result.content)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.failed_count, 0)
        self.assertIsNone(self.owner.locked_until)

    def test_passwordless_reset_and_self_change(self):
        reset = self.send(self.client, "post", f"/accounts/{self.staff.pk}/password", {
            "newPassword": "Replacement secret 2027!", "expectedRevision": 1})
        self.assertEqual(reset.status_code, 200, reset.content)
        changed = self.send(self.client, "post", "/auth/password", {"newPassword": "Owner new secret 2027!"})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(self.client.get("/api/v1/admin/me").status_code, 401)

    def test_revoked_operation_permission_rechecked_without_confirmation(self):
        group = PermissionGroup.objects.create(code="revoke", name="撤回权限")
        GroupPermission.objects.create(group=group, code="permission.manage")
        AccountGroup.objects.create(account=self.staff, group=group)
        client = self.login(self.staff)
        from accounts.security import require
        def revoke_after_require(request, code=None):
            result = require(request, code)
            GroupPermission.objects.filter(group=group).delete()
            return result
        with patch("accounts.views.require", side_effect=revoke_after_require):
            result = self.send(client, "post", "/permission-groups", {
                "code": "denied", "name": "不能创建", "permissionCodes": []})
        self.assertEqual(result.status_code, 403, result.content)
        self.assertFalse(PermissionGroup.objects.filter(code="denied").exists())

    def test_session_revoked_before_mutation_rejected(self):
        from accounts.security import require
        def revoke_after_require(request, code=None):
            result = require(request, code)
            AdminAccount.objects.filter(pk=self.owner.pk).update(auth_version=2)
            return result
        with patch("accounts.views.require", side_effect=revoke_after_require):
            result = self.send(self.client, "post", "/permission-groups", {
                "code": "stale", "name": "失效会话", "permissionCodes": []})
        self.assertEqual(result.status_code, 401, result.content)
        self.assertFalse(PermissionGroup.objects.filter(code="stale").exists())

    def test_compatibility_endpoint_keeps_action_and_read_permissions(self):
        group = PermissionGroup.objects.create(code="release-only", name="缺少读取权限")
        GroupPermission.objects.create(group=group, code="code.release.manage")
        AccountGroup.objects.create(account=self.staff, group=group)
        client = self.login(self.staff)
        for action in ["not-an-action", "account.create", "code.release.upload"]:
            result = self.send(client, "post", "/auth/confirm", {"action": action})
            self.assertEqual(result.status_code, 403, result.content)

    def test_business_hook_ignores_stale_header_but_rechecks_live_permissions(self):
        from accounts.security import confirm_action
        from django.test import RequestFactory
        request = RequestFactory().post("/", HTTP_X_ACTION_CONFIRMATION="stale-token")
        request.user = self.owner
        request.session = self.client.session
        request.request_id = uuid.uuid4()
        for action in ["page.publish", "payment.offline.confirm", "code.release.upload", "refund.offline.confirm"]:
            self.assertIsNone(confirm_action(request, self.owner, action, "business-object", 4))
        bad = confirm_action(request, self.owner, "unsupported.action", "business-object", 4)
        self.assertEqual(bad.status_code, 403)
        AdminAccount.objects.filter(pk=self.owner.pk).update(enabled=False)
        self.assertEqual(confirm_action(request, self.owner, "page.publish", "business-object", 4).status_code, 401)

    def test_business_hook_requires_same_actor_and_live_read_permission(self):
        from accounts.security import confirm_action
        from django.test import RequestFactory
        group = PermissionGroup.objects.create(code="write-only", name="只授予发布")
        GroupPermission.objects.create(group=group, code="code.release.manage")
        AccountGroup.objects.create(account=self.staff, group=group)
        client = self.login(self.staff)
        request = RequestFactory().post("/")
        request.user = self.staff
        request.session = client.session
        request.request_id = uuid.uuid4()
        denied = confirm_action(request, self.staff, "code.release.upload", "version", 1)
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(AuditLog.objects.filter(actor=self.staff, action_code="permission.denied",
            object_id="code.version.read").exists())
        GroupPermission.objects.create(group=group, code="code.version.read")
        self.assertEqual(confirm_action(request, self.owner, "code.release.upload", "version", 1).status_code, 401)

    def test_account_creation_keeps_body_group_and_duplicate_validation(self):
        group = PermissionGroup.objects.get(code="catalog_operator")
        body = {"loginName": "new-passwordless", "displayName": "新账号", "password": "New account password 2027!",
                "groupIds": [str(group.pk)]}
        for changed in [{"groupIds": []}, {"groupIds": [42]}, {"groupIds": ["invalid-uuid"]},
                        {"groupIds": ["00000000-0000-0000-0000-000000000000"]}, {"password": "x"}]:
            self.assertEqual(self.send(self.client, "post", "/accounts", {**body, **changed}).status_code, 400)
        self.assertEqual(self.send(self.client, "post", "/accounts", body).status_code, 201)
        self.assertEqual(self.send(self.client, "post", "/accounts", body).status_code, 409)
        self.assertEqual(self.client.get("/api/v1/admin/accounts").status_code, 200)

    def test_group_creation_keeps_validation_and_uniqueness_without_password(self):
        body = {"code": "unique-passwordless", "name": "免密码权限组", "permissionCodes": ["catalog.read"]}
        self.assertEqual(self.send(self.client, "post", "/permission-groups", {**body, "permissionCodes": ["unknown"]}).status_code, 400)
        self.assertEqual(self.send(self.client, "post", "/permission-groups", body).status_code, 201)
        self.assertEqual(self.send(self.client, "post", "/permission-groups", body).status_code, 409)
