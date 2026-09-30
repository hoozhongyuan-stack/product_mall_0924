from datetime import timedelta

from django.contrib.auth import logout
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from common.http import method
from .models import ActionConfirmation, AdminAccount, AuditLog
from .security import audit, error, lock_until, parse_json, require, require_live, response, validate_new_password


@sensitive_variables("password")
def _confirm_password(request, actor, password):
    if actor.locked_until and actor.locked_until > timezone.now():
        return error(request, 429, "RATE_LIMITED", "密码确认暂时受限，请稍后再试。")
    if actor.locked_until:
        actor.failed_count = 0
        actor.locked_until = None
    if not actor.check_password(password):
        actor.failed_count += 1
        actor.locked_until = lock_until() if actor.failed_count >= 5 else None
        actor.save(update_fields=["failed_count", "locked_until", "updated_at"])
        audit(request, "auth.confirm", "admin_account", actor.id, actor, result="DENIED")
        return error(request, 403, "CONFIRMATION_FAILED", "当前密码不正确。")
    actor.failed_count = 0
    actor.locked_until = None
    actor.save(update_fields=["failed_count", "locked_until", "updated_at"])
    return None


@sensitive_variables("body")
def _password_body(request, resetting):
    try:
        body = parse_json(request)
        if any(not isinstance(body.get(key), str) or not 1 <= len(body[key]) <= 1024
               for key in ("currentPassword", "newPassword")):
            raise ValueError("请填写当前密码和新密码，密码长度不能超过 1024 个字符。")
        if resetting and (type(body.get("expectedRevision")) is not int or body["expectedRevision"] < 0):
            raise ValueError("账号修订号不正确，请刷新列表后重试。")
        return body, None
    except ValueError as exc:
        return None, error(request, 400, "VALIDATION_FAILED", str(exc))


@sensitive_variables("body")
def _replace_password(request, actor, target, body, resetting):
    if resetting and target.revision != body["expectedRevision"]:
        return error(request, 409, "REVISION_CONFLICT", "账号已被其他人修改，请刷新列表后重试。")
    since = timezone.now() - timedelta(minutes=15)
    if AuditLog.objects.filter(actor=actor, occurred_at__gte=since, result="SUCCESS",
            action_code__in=["account.password.change", "account.password.reset"]).count() >= 20:
        return error(request, 429, "RATE_LIMITED", "密码修改过于频繁，请稍后再试。")
    bad = _confirm_password(request, actor, body["currentPassword"])
    if bad:
        return bad
    try:
        validate_new_password(body["newPassword"], target)
        if target.check_password(body["newPassword"]):
            raise ValueError("新密码不能与原密码相同。")
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    target.set_password(body["newPassword"])
    target.auth_version += 1
    target.revision += 1
    target.failed_count = 0
    target.locked_until = None
    target.save(update_fields=["password", "auth_version", "revision", "failed_count", "locked_until", "updated_at"])
    ActionConfirmation.objects.filter(actor=target, consumed_at__isnull=True).update(consumed_at=timezone.now())
    audit(request, "account.password.reset" if resetting else "account.password.change",
          "admin_account", target.pk, actor, after={"revision": target.revision, "sessionsRevoked": True})
    return None


@sensitive_post_parameters("currentPassword", "newPassword")
@sensitive_variables("body")
def password_view(request, account_id=None):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request)
    if bad:
        return bad
    resetting = account_id is not None
    if resetting and actor.kind != AdminAccount.Kind.OWNER:
        audit(request, "account.password.reset", "admin_account", account_id, actor, result="DENIED")
        return error(request, 403, "PERMISSION_DENIED", "只有主账号可以重置子账号密码。")
    body, bad = _password_body(request, resetting)
    if bad:
        return bad
    with transaction.atomic():
        actor = AdminAccount.objects.select_for_update().get(pk=actor.pk)
        _, bad = require_live(request)
        if bad:
            return bad
        if resetting and actor.kind != AdminAccount.Kind.OWNER:
            return error(request, 403, "PERMISSION_DENIED", "只有主账号可以重置子账号密码。")
        target = AdminAccount.objects.select_for_update().filter(pk=account_id).first() if resetting else actor
        if target is None:
            return error(request, 404, "NOT_FOUND", "账号不存在。")
        if resetting and target.kind != AdminAccount.Kind.STAFF:
            return error(request, 403, "PERMISSION_DENIED", "主账号请使用“修改我的密码”。")
        bad = _replace_password(request, actor, target, body, resetting)
        if bad:
            return bad
    if not resetting:
        logout(request)
    return response(request, {"accountId": str(target.pk), "revision": target.revision,
                              "requiresLogin": not resetting})
