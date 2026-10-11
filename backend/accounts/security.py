import hashlib
import hmac
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from common.http import response, error, parse_json_object
from .models import AdminAccount, AuditLog, GroupPermission

PERMISSION_CODES = frozenset({
    "dining.read", "dining.manage", "dining.finance.read", "dining.finance.manage",
    'stores.procurement.read', 'stores.procurement.manage', 'stores.procurement.finance',
    "stores.read", "stores.manage", "stores.accounts.read", "stores.accounts.manage",
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
    "notification.read", "notification.manage", "notification.recover",
    "code.version.read", "code.release.manage", "business.report.read", "business.report.export", "audit.export",
    "wechat.integration.read", "wechat.integration.manage",
})


ACTION_PERMISSIONS = {
    "account.create": "account.manage",
    "account.disable": "account.manage",
    "group.create": "permission.manage",
    "group.update": "permission.manage",
    "page.publish": "page.publish",
    "startup.publish": "startup.publish",
    "page.rollback": "page.publish",
    "startup.rollback": "startup.publish",
    "navigation.publish": "navigation.publish",
    "navigation.rollback": "navigation.publish",
    "customer_service.publish": "customer_service.publish",
    "customer_service.rollback": "customer_service.publish",
    "payment.offline.confirm": "payment.offline.confirm",
    "aftersale.review": "aftersale.review",
    "aftersale.return.accept": "aftersale.return.accept",
    "refund.offline.confirm": "refund.offline.confirm",
    "refund.wechat.dispatch": "refund.prepare",
    "refund.benefits.settle": "refund.prepare",
    "member.rules.update": "member.rules.manage",
    "coupon.publish": "coupon.publish",
    "coupon.issue": "coupon.issue",
    "exchange.publish": "exchange.publish",
    "notification.task.recover_reservation": "notification.recover",
    "stores.map.configure": "stores.manage",
    "stores.settlement.configure": "stores.manage",
    "stores.cost.batch.configure": "stores.manage",
    "stores.share.batch.configure": "stores.manage",
    "stores.profit.configure": "stores.manage",
    "stores.profit.batch.configure": "stores.manage",
    "stores.withdrawal.review": "stores.accounts.manage",
    "stores.procurement.configure": "stores.procurement.manage",
    "stores.procurement.confirm-payment": "stores.procurement.finance",
    "stores.procurement.close-cancel": "stores.procurement.finance",
    "stores.withdrawal.pay": "stores.accounts.manage",
    "stores.withdrawal.payee": "stores.accounts.manage",
    "wechat.integration.update": "wechat.integration.manage",
    "wechat.integration.test": "wechat.integration.manage",
    "code.release.upload_key": "code.release.manage",
    "code.release.developer_upload_key": "code.release.manage",
    "code.release.upload": "code.release.manage",
    "code.release.resolve_upload": "code.release.manage",
    "code.release.review": "code.release.manage",
    "code.release.resolve_review": "code.release.manage",
    "code.release.publish": "code.release.manage"
}

ACTION_READ_PERMISSIONS = {
    "page.rollback": "page.read",
    "startup.rollback": "startup.read",
    "navigation.rollback": "navigation.read",
    "customer_service.rollback": "customer_service.read",
    "notification.task.recover_reservation": "notification.read",
    "wechat.integration.update": "wechat.integration.read",
    "wechat.integration.test": "wechat.integration.read",
    "code.release.upload_key": "code.version.read",
    "code.release.developer_upload_key": "code.version.read",
    "code.release.upload": "code.version.read",
    "code.release.resolve_upload": "code.version.read",
    "code.release.review": "code.version.read",
    "code.release.resolve_review": "code.version.read",
    "code.release.publish": "code.version.read"
}


def parse_json(request):
    return parse_json_object(request)


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
    """Authorize a business action without requesting the operator password.

    Existing domain entry points retain this hook so permission and session
    revocation are checked live after lock waits. Object/revision and idempotency
    checks remain owned by the business operation; legacy headers are ignored.
    """
    code = ACTION_PERMISSIONS.get(action)
    if code is None:
        return error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
    live, bad = require_live(request, code)
    if bad:
        return bad
    if live.pk != account.pk or live.auth_version != account.auth_version:
        return error(request, 401, "SESSION_EXPIRED", "请重新登录。")
    read_code = ACTION_READ_PERMISSIONS.get(action)
    if read_code and read_code not in permissions(live):
        audit(request, "permission.denied", "permission", read_code, actor=live, result="DENIED")
        return error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
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
