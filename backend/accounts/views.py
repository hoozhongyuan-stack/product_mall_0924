import hashlib
import time
import uuid
from datetime import timedelta

from django.contrib.auth import login, logout
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils import timezone

from common.http import method
from .models import AccountGroup, ActionConfirmation, AdminAccount, AuditLog, GroupPermission, PermissionGroup
from .security import (PERMISSION_CODES, audit, bump_versions, confirm_action, current_account,
                       error, lock_until, parse_json, permissions, require, response,
                       source_fingerprint, validate_new_password)

DUMMY_PASSWORD_HASH = make_password("unused-login-timing-value")


def body_or_error(request):
    try:
        return parse_json(request), None
    except ValueError as exc:
        return None, error(request, 400, "VALIDATION_FAILED", str(exc))


def account_data(account):
    return {"accountId": str(account.id), "loginName": account.login_name,
            "displayName": account.display_name, "kind": account.kind,
            "enabled": account.enabled, "revision": account.revision,
            "groupIds": [str(value) for value in account.permission_groups.values_list("id", flat=True)]}


def group_data(group):
    return {"groupId": str(group.id), "code": group.code, "name": group.name,
            "enabled": group.enabled, "revision": group.revision,
            "permissionCodes": list(group.permissions.order_by("code").values_list("code", flat=True))}


@ensure_csrf_cookie
def csrf_token(request):
    bad = method(request, "GET")
    return bad or response(request)


def login_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    body, bad = body_or_error(request)
    if bad:
        return bad
    name, password = body.get("loginName"), body.get("password")
    if not isinstance(name, str) or not isinstance(password, str) or not (1 <= len(name) <= 150):
        return error(request, 400, "VALIDATION_FAILED", "账号或密码格式不正确。")
    source = source_fingerprint(request)
    since = timezone.now() - timedelta(minutes=15)
    recent = AuditLog.objects.filter(action_code="auth.login", result="FAILED",
                                     occurred_at__gte=since, after__source=source).count()
    if recent >= 10:
        audit(request, "auth.login", "admin_account", after={"source": source}, result="DENIED")
        return error(request, 429, "RATE_LIMITED", "登录尝试过多，请稍后再试。")
    with transaction.atomic():
        account = AdminAccount.objects.select_for_update().filter(login_name=name.strip().lower()).first()
        if account and account.locked_until and account.locked_until > timezone.now():
            audit(request, "auth.login", "admin_account", account.id, account,
                  after={"source": source}, result="DENIED")
            return error(request, 429, "RATE_LIMITED", "登录暂时受限，请稍后再试。")
        if account and account.locked_until and account.locked_until <= timezone.now():
            account.failed_count = 0
            account.locked_until = None
        if not account or not account.enabled:
            check_password(password, DUMMY_PASSWORD_HASH)
            valid_password = False
        else:
            valid_password = account.check_password(password)
        if not valid_password:
            if account and account.enabled:
                account.failed_count += 1
                account.locked_until = lock_until() if account.failed_count >= 5 else None
                account.save(update_fields=["failed_count", "locked_until", "updated_at"])
            audit(request, "auth.login", "admin_account", account.id if account else "", account,
                  after={"source": source}, result="FAILED")
            return error(request, 401, "INVALID_CREDENTIALS", "账号或密码不正确。")
        account.failed_count = 0
        account.locked_until = None
        account.last_active_at = timezone.now()
        account.save(update_fields=["failed_count", "locked_until", "last_active_at", "updated_at"])
        audit(request, "auth.login", "admin_account", account.id, account)
    login(request, account, backend="django.contrib.auth.backends.ModelBackend")
    request.session["admin_auth_version"] = account.auth_version
    request.session["admin_last_active"] = time.time()
    return response(request, {**account_data(account), "permissionCodes": permissions(account)})


def logout_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    account, bad = require(request)
    if bad:
        return bad
    audit(request, "auth.logout", "admin_account", account.id, account)
    logout(request)
    return response(request)


def me_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    account, bad = require(request)
    return bad or response(request, {**account_data(account), "permissionCodes": permissions(account)})


def confirm_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    account, bad = require(request)
    if bad:
        return bad
    body, bad = body_or_error(request)
    if bad:
        return bad
    action = body.get("action")
    required = {"account.create": "account.manage", "account.disable": "account.manage",
                "group.create": "permission.manage", "group.update": "permission.manage",
                "page.publish": "page.publish", "startup.publish": "startup.publish",
                "page.rollback": "page.publish", "startup.rollback": "startup.publish",
                "navigation.publish": "navigation.publish", "navigation.rollback": "navigation.publish",
                "customer_service.publish": "customer_service.publish",
                "customer_service.rollback": "customer_service.publish",
                "payment.offline.confirm": "payment.offline.confirm",
                "aftersale.review": "aftersale.review", "aftersale.return.accept": "aftersale.return.accept", "refund.offline.confirm": "refund.offline.confirm",
                "refund.wechat.dispatch": "refund.prepare",
                "refund.benefits.settle": "refund.prepare",
                "member.rules.update": "member.rules.manage",
                "coupon.publish": "coupon.publish", "coupon.issue": "coupon.issue",
                "exchange.publish": "exchange.publish",
                "notification.task.recover_reservation": "notification.recover"}
    if not isinstance(action, str) or action not in required or required[action] not in permissions(account):
        return error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
    read_permission = {"page.rollback": "page.read", "startup.rollback": "startup.read",
                       "navigation.rollback": "navigation.read",
                       "customer_service.rollback": "customer_service.read",
                       "notification.task.recover_reservation": "notification.read"}.get(action)
    if read_permission and read_permission not in permissions(account):
        return error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
    with transaction.atomic():
        live = AdminAccount.objects.select_for_update().get(pk=account.pk)
        if not live.enabled or required[action] not in permissions(live) or (
                read_permission and read_permission not in permissions(live)):
            return error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
        if live.locked_until and live.locked_until > timezone.now():
            audit(request, "auth.confirm", "admin_account", live.id, live, result="DENIED")
            return error(request, 429, "RATE_LIMITED", "操作确认暂时受限，请稍后再试。")
        if live.locked_until and live.locked_until <= timezone.now():
            live.failed_count = 0
            live.locked_until = None
        if not isinstance(body.get("password"), str) or not live.check_password(body["password"]):
            live.failed_count += 1
            live.locked_until = lock_until() if live.failed_count >= 5 else None
            live.save(update_fields=["failed_count", "locked_until", "updated_at"])
            audit(request, "auth.confirm", "admin_account", live.id, live, result="DENIED")
            return error(request, 403, "CONFIRMATION_FAILED", "当前密码不正确。")
        if live.failed_count or live.locked_until:
            live.failed_count = 0
            live.locked_until = None
            live.save(update_fields=["failed_count", "locked_until", "updated_at"])
    object_id = body.get("objectId", "")
    revision = body.get("revision", 0)
    if not isinstance(object_id, str) or type(revision) is not int or revision < 0:
        return error(request, 400, "VALIDATION_FAILED", "确认参数不正确。")
    token = uuid.uuid4().hex
    with transaction.atomic():
        ActionConfirmation.objects.filter(actor=account, session_key=request.session.session_key,
            consumed_at__isnull=True).update(consumed_at=timezone.now())
        ActionConfirmation.objects.create(token_hash=hashlib.sha256(token.encode()).hexdigest(),
            actor=account, session_key=request.session.session_key, action=action,
            object_id=object_id, revision=revision, expires_at=timezone.now() + timedelta(minutes=5))
    return response(request, {"confirmationToken": token, "expiresInSeconds": 300})


def accounts_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    code = "account.read" if request.method == "GET" else "account.manage"
    account, bad = require(request, code)
    if bad:
        return bad
    if request.method == "GET":
        return response(request, [account_data(item) for item in AdminAccount.objects.order_by("created_at")])
    body, bad = body_or_error(request)
    if bad:
        return bad
    bad = confirm_action(request, account, "account.create", "", 0)
    if bad:
        return bad
    name, display = body.get("loginName"), body.get("displayName")
    group_ids = body.get("groupIds")
    if (not isinstance(name, str) or not isinstance(display, str) or
            not 1 <= len(name.strip()) <= 150 or not 1 <= len(display.strip()) <= 120 or
            not isinstance(group_ids, list) or not group_ids or len(group_ids) > 10):
        return error(request, 400, "VALIDATION_FAILED", "账号资料或权限组不正确。")
    try:
        if any(not isinstance(value, str) for value in group_ids):
            raise ValueError("权限组 ID 格式不正确。")
        ids = [uuid.UUID(value) for value in group_ids]
        validate_new_password(body.get("password"))
    except (ValueError, TypeError) as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    groups = list(PermissionGroup.objects.filter(id__in=ids, enabled=True))
    if len(groups) != len(set(ids)):
        return error(request, 400, "VALIDATION_FAILED", "权限组不存在或已停用。")
    if account.kind == AdminAccount.Kind.STAFF:
        assigned = set(GroupPermission.objects.filter(group__in=groups).values_list("code", flat=True))
        if not assigned.issubset(set(permissions(account))):
            audit(request, "account.create", "admin_account", actor=account, result="DENIED")
            return error(request, 403, "PERMISSION_DENIED", "不能授予高于自身的权限。")
    try:
        with transaction.atomic():
            created = AdminAccount.objects.create_user(name, body["password"], display_name=display.strip())
            AccountGroup.objects.bulk_create([AccountGroup(account=created, group=group) for group in groups])
            audit(request, "account.create", "admin_account", created.id, account,
                  after={"enabled": True, "groupIds": [str(group.id) for group in groups]})
    except IntegrityError:
        return error(request, 409, "ACCOUNT_DUPLICATE", "登录名已存在。")
    return response(request, account_data(created), 201)


def account_detail_view(request, account_id):
    bad = method(request, "PATCH")
    if bad:
        return bad
    actor, bad = require(request, "account.manage")
    if bad:
        return bad
    body, bad = body_or_error(request)
    if bad:
        return bad
    if body.get("enabled") is not False or type(body.get("expectedRevision")) is not int:
        return error(request, 400, "VALIDATION_FAILED", "仅支持按修订号停用子账号。")
    bad = confirm_action(request, actor, "account.disable", account_id, body["expectedRevision"])
    if bad:
        return bad
    with transaction.atomic():
        target = AdminAccount.objects.select_for_update().filter(id=account_id).first()
        if not target:
            return error(request, 404, "NOT_FOUND", "账号不存在。")
        if target.kind == AdminAccount.Kind.OWNER or target.id == actor.id:
            audit(request, "account.disable", "admin_account", target.id, actor, result="DENIED")
            return error(request, 403, "PERMISSION_DENIED", "不能停用主账号或自己。")
        if target.revision != body["expectedRevision"]:
            return error(request, 409, "REVISION_CONFLICT", "账号已被其他人修改。")
        before = {"enabled": target.enabled}
        target.enabled = False
        target.revision += 1
        target.auth_version += 1
        target.save(update_fields=["enabled", "revision", "auth_version", "updated_at"])
        audit(request, "account.disable", "admin_account", target.id, actor,
              before=before, after={"enabled": False})
    return response(request, account_data(target))


def groups_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    account, bad = require(request, "permission.read" if request.method == "GET" else "permission.manage")
    if bad:
        return bad
    if request.method == "GET":
        return response(request, [group_data(group) for group in PermissionGroup.objects.order_by("code")])
    body, bad = body_or_error(request)
    if bad:
        return bad
    bad = confirm_action(request, account, "group.create", "", 0)
    if bad:
        return bad
    code, name, codes = body.get("code"), body.get("name"), body.get("permissionCodes")
    if (not isinstance(code, str) or not isinstance(name, str) or
            not 1 <= len(code) <= 60 or not 1 <= len(name) <= 100 or
            not isinstance(codes, list) or any(not isinstance(item, str) or item not in PERMISSION_CODES for item in codes)):
        return error(request, 400, "VALIDATION_FAILED", "权限组内容不正确。")
    if account.kind == AdminAccount.Kind.STAFF and not set(codes).issubset(set(permissions(account))):
        audit(request, "group.create", "permission_group", actor=account, result="DENIED")
        return error(request, 403, "PERMISSION_DENIED", "不能授予高于自身的权限。")
    try:
        with transaction.atomic():
            group = PermissionGroup.objects.create(code=code, name=name)
            GroupPermission.objects.bulk_create([GroupPermission(group=group, code=item) for item in set(codes)])
            audit(request, "group.create", "permission_group", group.id, account,
                  after={"permissionCodes": sorted(set(codes))})
    except IntegrityError:
        return error(request, 409, "GROUP_DUPLICATE", "权限组编码或名称已存在。")
    return response(request, group_data(group), 201)


def group_detail_view(request, group_id):
    bad = method(request, "PATCH")
    if bad:
        return bad
    actor, bad = require(request, "permission.manage")
    if bad:
        return bad
    body, bad = body_or_error(request)
    if bad:
        return bad
    codes, revision = body.get("permissionCodes"), body.get("expectedRevision")
    if (not isinstance(codes, list) or type(revision) is not int or
            any(not isinstance(item, str) or item not in PERMISSION_CODES for item in codes)):
        return error(request, 400, "VALIDATION_FAILED", "权限组内容不正确。")
    bad = confirm_action(request, actor, "group.update", group_id, revision)
    if bad:
        return bad
    with transaction.atomic():
        group = PermissionGroup.objects.select_for_update().filter(id=group_id).first()
        if not group:
            return error(request, 404, "NOT_FOUND", "权限组不存在。")
        if group.revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "权限组已被其他人修改。")
        if actor.kind == AdminAccount.Kind.STAFF and (
            group.accounts.filter(id=actor.id).exists() or
            not set(codes).issubset(set(permissions(actor)))
        ):
            audit(request, "group.update", "permission_group", group.id, actor, result="DENIED")
            return error(request, 403, "PERMISSION_DENIED", "不能修改自己的权限组或提升自身权限。")
        before = sorted(group.permissions.values_list("code", flat=True))
        GroupPermission.objects.filter(group=group).delete()
        GroupPermission.objects.bulk_create([GroupPermission(group=group, code=item) for item in set(codes)])
        group.revision += 1
        group.save(update_fields=["revision", "updated_at"])
        bump_versions(AdminAccount.objects.filter(accountgroup__group=group))
        audit(request, "group.update", "permission_group", group.id, actor,
              before={"permissionCodes": before}, after={"permissionCodes": sorted(set(codes))})
    return response(request, group_data(group))
