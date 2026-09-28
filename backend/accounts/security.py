import hashlib
import hmac
import json
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from .models import ActionConfirmation, AdminAccount, AuditLog, GroupPermission

PERMISSION_CODES = frozenset({
    "catalog.read", "catalog.write", "sku.status.write", "sku.unit.write", "sku.price.write",
    "asset.read", "asset.upload", "asset.delete", "page.read", "page.edit", "page.publish",
    "startup.read", "startup.edit", "startup.publish", "account.read", "account.manage",
    "navigation.read", "navigation.edit", "navigation.publish",
    "customer_service.read", "customer_service.edit", "customer_service.publish",
    "account.reset_credentials", "account.unlock", "permission.read", "permission.manage", "audit.read",
    "inventory.read", "inventory.manage", "inventory.review",
    "settlement.shipping.manage", "order.read", "payment.settings.manage", "payment.offline.confirm",
    "fulfillment.read", "fulfillment.ship", "fulfillment.settings.manage",
    "fulfillment.redeem", "fulfillment.redeem.reverse",
    "aftersale.read", "aftersale.review", "aftersale.return.accept", "refund.prepare", "refund.offline.confirm",
    "member.read", "member.rules.read", "member.rules.manage",
    "coupon.read", "coupon.manage", "coupon.publish", "coupon.issue", "coupon.issue.repeat",
    "exchange.read", "exchange.manage", "exchange.publish",
})


def response(request, data=None, status=200):
    payload = {"success": True, "data": data if data is not None else {}, "requestId": str(request.request_id)}
    return JsonResponse(payload, status=status)


def error(request, status, code, message, details=None):
    return JsonResponse({"success": False, "error": {"code": code, "message": message, "details": details or []},
                         "requestId": str(request.request_id)}, status=status)


def parse_json(request):
    if request.content_type != "application/json" or len(request.body) > 32768:
        raise ValueError("请发送不超过 32 KB 的 JSON 请求。")
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("JSON 格式不正确。") from exc
    if not isinstance(body, dict):
        raise ValueError("请求内容必须是对象。")
    return body


def audit(request, action, obj_type, obj_id="", actor=None, before=None, after=None, result="SUCCESS"):
    return AuditLog.objects.create(actor=actor, action_code=action, object_type=obj_type,
                                   object_id=str(obj_id), before=before or {}, after=after or {},
                                   result=result, request_id=request.request_id)


def source_fingerprint(request):
    address = request.META.get("REMOTE_ADDR", "unknown")
    return hmac.new(settings.SECRET_KEY.encode(), address.encode(), hashlib.sha256).hexdigest()


def permissions(account):
    if account.kind == AdminAccount.Kind.OWNER:
        return sorted(PERMISSION_CODES)
    return sorted(set(GroupPermission.objects.filter(
        group__accountgroup__account=account, group__enabled=True,
    ).values_list("code", flat=True)))


def operation_permissions(account):
    """Current capabilities for a trusted domain operation after lock waits."""
    fresh = AdminAccount.objects.filter(pk=account.pk, enabled=True).first()
    if fresh is None or fresh.auth_version != account.auth_version:
        return []
    return permissions(fresh)


def current_account(request):
    account = request.user
    if not account.is_authenticated or not account.enabled:
        return None
    last_active = request.session.get("admin_last_active")
    if (request.session.get("admin_auth_version") != account.auth_version or
            not isinstance(last_active, (int, float)) or time.time() - last_active > 1800):
        logout(request)
        return None
    request.session["admin_last_active"] = time.time()
    return account


def require(request, code=None):
    account = current_account(request)
    if account is None:
        return None, error(request, 401, "SESSION_EXPIRED", "请重新登录。")
    if code and code not in permissions(account):
        audit(request, "permission.denied", "permission", code, actor=account, result="DENIED")
        return None, error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
    return account, None


def require_live(request, code=None):
    """Revalidate a session after waiting for a domain lock.

    AuthenticationMiddleware caches request.user for the request lifetime. A
    blocked operation must see account disablement and auth-version changes
    made while it waited, as well as the current permission memberships.
    """
    cached = request.user
    if not cached.is_authenticated:
        return require(request, code)
    fresh = AdminAccount.objects.filter(pk=cached.pk).first()
    if fresh is None:
        logout(request)
        return None, error(request, 401, "SESSION_EXPIRED", "请重新登录。")
    request.user = fresh
    request._cached_user = fresh
    return require(request, code)


def confirm_action(request, account, action, object_id, revision):
    token = request.headers.get("X-Action-Confirmation", "")
    if not token or len(token) > 128:
        return error(request, 403, "CONFIRMATION_REQUIRED", "请先完成二次确认。")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        pending = ActionConfirmation.objects.select_for_update().filter(token_hash=digest).first()
        if not pending or pending.consumed_at:
            return error(request, 403, "CONFIRMATION_REQUIRED", "二次确认已失效。")
        pending.consumed_at = timezone.now()
        pending.save(update_fields=["consumed_at"])
        if (pending.actor_id != account.id or pending.session_key != request.session.session_key or
                pending.action != action or pending.object_id != str(object_id) or
                pending.revision != revision or pending.expires_at < timezone.now()):
            return error(request, 403, "CONFIRMATION_REQUIRED", "二次确认已失效。")
    return None


def validate_new_password(password, account=None):
    if not isinstance(password, str):
        raise ValueError("密码格式不正确。")
    try:
        validate_password(password, user=account)
    except ValidationError as exc:
        raise ValueError("密码强度不足：" + " ".join(exc.messages)) from exc


def bump_versions(accounts):
    ids = list(accounts.values_list("id", flat=True))
    if ids:
        from django.db.models import F
        AdminAccount.objects.filter(id__in=ids).update(auth_version=F("auth_version") + 1)


def lock_until():
    return timezone.now() + timedelta(minutes=15)
