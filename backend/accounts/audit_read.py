"""Permissioned, bounded audit observation; stored evidence is never rewritten."""

import hashlib
import json
import re
import uuid
from itertools import islice

from django.core import signing
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache

from common.http import cursor_response, method
from .models import AuditLog
from .read_window import read_window
from .read_rate_limit import read_rate_limit
from .security import error, require


QUERY_KEYS = frozenset({"from", "to", "actorId", "actionCode", "objectType", "objectId", "result", "limit", "cursor"})
SALT = "audit-list-v1"
SENSITIVE_KEY = re.compile(r"password|secret|token|credential|cookie|authorization|openid|phone|mobile|address|recipient|private|merchant|account|source|email|note|reason|detail|description|name|text|url|path|key|digest|hash|card|bank", re.I)
SAFE_STRING_KEYS = frozenset({"status", "state", "action", "kind", "type", "channel", "outcome",
                              "result", "mode", "saleStatus", "paymentMethod", "orderKind", "eventType"})
SAFE_ENUM_VALUES = frozenset({
    "DRAFT", "ON_SALE", "OFF_SALE", "ACTIVE", "INACTIVE", "ENABLED", "DISABLED",
    "SUCCESS", "FAILED", "DENIED", "PENDING", "READY", "BLOCKED", "UNKNOWN",
    "PAID", "CLOSED", "PENDING_PAYMENT", "PREPARED", "PROCESSING", "SUCCEEDED",
    "WAITING_REFUND", "WAITING_RETURN", "COMPLETED", "REJECTED", "WITHDRAWN",
    "WECHAT", "OFFLINE", "POINTS", "CASH", "SHIP", "REDEEM", "REFUND_ONLY",
    "RETURN_REFUND", "RESERVED", "CLAIMED", "RETRY_WAIT", "SIMULATED", "NOT_CONFIGURED",
})
SAFE_NUMBER_KEYS = frozenset({"revision", "expectedRevision", "quantity", "count", "amountFen",
                              "priceFen", "feeFen", "sortOrder", "attemptCount", "fileCount"})
SAFE_BOOLEAN_KEYS = frozenset({"enabled", "active", "published", "recoverable"})


def _safe_json(value, key="", depth=0, budget=None):
    if budget is None:
        budget = [150]
    budget[0] -= 1
    if budget[0] < 0:
        return "[内容已截断]"
    if SENSITIVE_KEY.search(key):
        return "[已脱敏]"
    if depth >= 8:
        return "[层级过深]"
    if isinstance(value, dict):
        result = {}
        for raw_key, item in islice(value.items(), 100):
            child_key = str(raw_key)
            safe_key = child_key if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", child_key) else "[字段已脱敏]"
            result[safe_key] = _safe_json(item, child_key, depth + 1, budget)
            if budget[0] <= 0:
                break
        return result
    if isinstance(value, list):
        result = []
        for item in value[:100]:
            result.append(_safe_json(item, key, depth + 1, budget))
            if budget[0] <= 0:
                break
        return result
    if isinstance(value, str):
        return value if key in SAFE_STRING_KEYS and value in SAFE_ENUM_VALUES else "[已脱敏]"
    if isinstance(value, bool):
        return value if key in SAFE_BOOLEAN_KEYS else "[已脱敏]"
    if isinstance(value, (int, float)):
        return value if key in SAFE_NUMBER_KEYS else "[已脱敏]"
    return None if value is None else "[已脱敏]"


def _filters(request):
    params = request.GET
    if set(params) - QUERY_KEYS or any(len(params.getlist(key)) != 1 for key in params):
        raise ValueError("筛选参数不正确。")
    first, last, start, end = read_window(params)
    actor = params.get("actorId", "")
    if actor:
        try:
            actor = uuid.UUID(actor)
        except ValueError as exc:
            raise ValueError("操作者 ID 不正确。") from exc
    action = params.get("actionCode", "")
    obj_type = params.get("objectType", "")
    obj_id = params.get("objectId", "")
    result = params.get("result", "")
    if ((action and not re.fullmatch(r"[\w.:-]{1,80}", action, re.ASCII)) or
            (obj_type and not re.fullmatch(r"[\w.:-]{1,80}", obj_type, re.ASCII)) or
            (obj_id and not re.fullmatch(r"[\w.:-]{1,100}", obj_id, re.ASCII)) or
            result not in {"", *AuditLog.Result.values}):
        raise ValueError("筛选值不正确。")
    raw_limit = params.get("limit", "20")
    if not re.fullmatch(r"[1-9][0-9]*", raw_limit) or int(raw_limit) > 100:
        raise ValueError("每页数量必须在 1 至 100 之间。")
    if len(params.get("cursor", "")) > 2048:
        raise ValueError("游标不正确。")
    return first, last, start, end, actor, action, obj_type, obj_id, result, int(raw_limit)


def _fingerprint(filters):
    first, last, _, _, actor, action, obj_type, obj_id, result, _ = filters
    data = [first.isoformat(), last.isoformat(), str(actor), action, obj_type, obj_id, result]
    return hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()


def _position(token, fingerprint):
    try:
        payload = signing.loads(token, salt=SALT, max_age=3600)
        if not isinstance(payload, dict) or set(payload) != {"f", "at", "id"} or payload["f"] != fingerprint:
            raise ValueError
        at = parse_datetime(payload["at"])
        row_id = uuid.UUID(payload["id"])
        if at is None or not timezone.is_aware(at):
            raise ValueError
        return at, row_id
    except (signing.BadSignature, ValueError, TypeError, KeyError) as exc:
        raise ValueError("游标已失效，请重新查询。") from exc


def audit_queryset(start, end, actor_filter="", action="", obj_type="", obj_id="", result=""):
    """One filter path for paginated observation and bounded CSV production."""
    rows = AuditLog.objects.filter(occurred_at__gte=start, occurred_at__lt=end)
    if actor_filter:
        rows = rows.filter(actor_id=actor_filter)
    if action:
        rows = rows.filter(action_code=action)
    if obj_type:
        rows = rows.filter(object_type=obj_type)
    if obj_id:
        rows = rows.filter(object_id=obj_id)
    if result:
        rows = rows.filter(result=result)
    return rows


def audit_entry(row):
    return {"id": str(row.id), "actorId": str(row.actor_id) if row.actor_id else None,
            "actionCode": row.action_code, "objectType": row.object_type, "objectId": row.object_id,
            "before": _safe_json(row.before), "after": _safe_json(row.after), "result": row.result,
            "requestId": str(row.request_id), "occurredAt": row.occurred_at.isoformat()}


@never_cache
def audit_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    actor, bad = require(request, "audit.read")
    if bad:
        return bad
    try:
        filters = _filters(request)
        first, last, start, end, actor_filter, action, obj_type, obj_id, result, limit = filters
        fingerprint = _fingerprint(filters)
        position = _position(request.GET["cursor"], fingerprint) if request.GET.get("cursor") else None
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    limited = read_rate_limit(request, actor, "audit")
    if limited:
        return limited
    rows = audit_queryset(start, end, actor_filter, action, obj_type, obj_id, result)
    if position:
        rows = rows.filter(Q(occurred_at__lt=position[0]) | Q(occurred_at=position[0], id__lt=position[1]))
    rows = list(rows.order_by("-occurred_at", "-id")[:limit + 1])
    next_cursor = None
    if len(rows) > limit:
        last_row = rows[limit - 1]
        next_cursor = signing.dumps({"f": fingerprint, "at": last_row.occurred_at.isoformat(),
                                     "id": str(last_row.id)}, salt=SALT)
    items = [audit_entry(row) for row in rows[:limit]]
    return cursor_response(request, {"items": items, "nextCursor": next_cursor,
                              "from": first.isoformat(), "to": last.isoformat(), "timeZone": "Asia/Shanghai"})
