"""Bounded admin observation and fenced, pre-dispatch-only task recovery."""

import hashlib
import json
import re
import uuid
from datetime import date, datetime, time, timedelta
from math import ceil

from django.core import signing
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache

from accounts.models import AdminAccount, AuditLog
from accounts.security import audit, confirm_action, error, parse_json, require, require_live, response
from accounts.views import method

from .models import EVENT_TYPES, MessageAttempt, MessageTask


STATUSES = frozenset({"BLOCKED", "READY", "RESERVED", "CLAIMED", "RETRY_WAIT", "UNKNOWN", "FAILED", "SIMULATED"})
QUERY_KEYS = frozenset({"eventType", "status", "createdFrom", "createdTo", "limit", "cursor"})
CURSOR_SALT = "subscription-message-task-list-v1"
RECOVERY_ACTION = "notification.task.recover_reservation"
RECOVERY_LIMIT = 10


def _timestamp(value):
    return value.isoformat() if value else None


def _summary(task, now):
    return {"taskId": str(task.pk), "eventType": task.event_type, "status": task.status,
            "reasonCode": task.reason_code, "attemptCount": task.attempt_count,
            "occurredAt": _timestamp(task.occurred_at), "createdAt": _timestamp(task.created_at),
            "updatedAt": _timestamp(task.updated_at), "nextAttemptAt": _timestamp(task.next_attempt_at),
            "leaseUntil": _timestamp(task.lease_until),
            "recoverable": task.status == "RESERVED" and task.lease_until is not None and
                           task.lease_until <= now and not task.has_lease_attempt}


def _with_attempt_flag(queryset):
    return queryset.annotate(has_lease_attempt=Exists(MessageAttempt.objects.filter(
        task_id=OuterRef("pk"), lease_token=OuterRef("lease_token"))))


def _date_bound(raw, *, end=False):
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            parsed = date.fromisoformat(raw)
            start = timezone.make_aware(datetime.combine(parsed, time.min))
            return start + timedelta(days=1) if end else start
        except OverflowError as exc:
            raise ValueError("时间筛选超出支持范围。") from exc
    parsed = parse_datetime(raw)
    if parsed is None or not timezone.is_aware(parsed):
        raise ValueError("时间筛选必须是日期或带时区的时间。")
    return parsed


def _filters(request):
    params = request.GET
    if set(params) - QUERY_KEYS or any(len(params.getlist(key)) != 1 for key in params):
        raise ValueError("筛选参数不正确。")
    event_type = params.get("eventType", "")
    status = params.get("status", "")
    if event_type and event_type not in EVENT_TYPES or status and status not in STATUSES:
        raise ValueError("消息事件或任务状态不正确。")
    limit_raw = params.get("limit", "20")
    if not re.fullmatch(r"[1-9][0-9]*", limit_raw) or int(limit_raw) > 100:
        raise ValueError("每页数量必须在 1 至 100 之间。")
    local_today = timezone.localdate()
    start = _date_bound(params.get("createdFrom", "")) or timezone.make_aware(
        datetime.combine(local_today - timedelta(days=89), time.min))
    end = _date_bound(params.get("createdTo", ""), end=True) or timezone.make_aware(
        datetime.combine(local_today + timedelta(days=1), time.min))
    if start >= end or end - start > timedelta(days=90):
        raise ValueError("时间范围必须在 90 天内。")
    if params.get("cursor", "") and len(params["cursor"]) > 2048:
        raise ValueError("游标不正确。")
    return event_type, status, start, end, int(limit_raw)


def _fingerprint(filters):
    event_type, status, start, end, _ = filters
    payload = [event_type, status, _timestamp(start), _timestamp(end)]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _cursor_position(token, fingerprint):
    try:
        payload = signing.loads(token, salt=CURSOR_SALT, max_age=3600)
        if (not isinstance(payload, dict) or set(payload) != {"v", "f", "at", "id"} or
                payload["v"] != 1 or payload["f"] != fingerprint):
            raise ValueError
        created_at = parse_datetime(payload["at"])
        task_id = uuid.UUID(payload["id"])
        if created_at is None or not timezone.is_aware(created_at):
            raise ValueError
        return created_at, task_id
    except (signing.BadSignature, ValueError, TypeError, KeyError) as exc:
        raise ValueError("游标已失效，请重新查询。") from exc


@never_cache
def tasks_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "notification.read")
    if bad:
        return bad
    try:
        filters = _filters(request)
        event_type, status, start, end, limit = filters
        fingerprint = _fingerprint(filters)
        position = _cursor_position(request.GET["cursor"], fingerprint) if request.GET.get("cursor") else None
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    rows = _with_attempt_flag(MessageTask.objects.all())
    if event_type:
        rows = rows.filter(event_type=event_type)
    if status:
        rows = rows.filter(status=status)
    if start:
        rows = rows.filter(created_at__gte=start)
    if end:
        rows = rows.filter(created_at__lt=end)
    if position:
        rows = rows.filter(Q(created_at__lt=position[0]) |
                           Q(created_at=position[0], id__lt=position[1]))
    rows = list(rows.order_by("-created_at", "-id")[:limit + 1])
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = signing.dumps({"v": 1, "f": fingerprint, "at": last.created_at.isoformat(),
                                     "id": str(last.pk)}, salt=CURSOR_SALT)
    now = timezone.now()
    return response(request, {"items": [_summary(row, now) for row in rows[:limit]],
                              "nextCursor": next_cursor})


@never_cache
def task_detail_view(request, task_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "notification.read")
    if bad:
        return bad
    task = _with_attempt_flag(MessageTask.objects.all()).filter(pk=task_id).first()
    if task is None:
        return error(request, 404, "NOT_FOUND", "消息任务不存在。")
    attempts = [{"ordinal": attempt.ordinal, "outcome": attempt.outcome,
                 "failureCode": attempt.failure_code, "startedAt": _timestamp(attempt.started_at),
                 "finishedAt": _timestamp(attempt.finished_at)}
                for attempt in task.attempts.order_by("ordinal")]
    return response(request, {**_summary(task, timezone.now()), "attempts": attempts})


def _recovery_limit(request, actor):
    recent = AuditLog.objects.filter(actor=actor, action_code=RECOVERY_ACTION, result="SUCCESS",
                                     occurred_at__gte=timezone.now() - timedelta(hours=1)).order_by("occurred_at")
    if recent.count() < RECOVERY_LIMIT:
        return None
    oldest = recent.values_list("occurred_at", flat=True).first()
    retry_after = max(1, ceil((oldest + timedelta(hours=1) - timezone.now()).total_seconds()))
    limited = error(request, 429, "RATE_LIMITED", "过去一小时的任务恢复次数已达上限，请稍后再试。")
    limited["Retry-After"] = str(retry_after)
    return limited


@never_cache
def recover_reservation_view(request, task_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "notification.recover")
    if bad:
        return bad
    _, bad = require(request, "notification.read")
    if bad:
        return bad
    try:
        body = parse_json(request)
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    if (set(body) != {"expectedUpdatedAt", "expectedAttemptCount"} or
            type(body["expectedAttemptCount"]) is not int or body["expectedAttemptCount"] < 0 or
            not isinstance(body["expectedUpdatedAt"], str) or
            parse_datetime(body["expectedUpdatedAt"]) is None or
            not timezone.is_aware(parse_datetime(body["expectedUpdatedAt"]))):
        return error(request, 400, "VALIDATION_FAILED", "任务版本参数不正确。")
    with transaction.atomic():
        task = _with_attempt_flag(MessageTask.objects.select_for_update()).filter(pk=task_id).first()
        if task is None:
            return error(request, 404, "NOT_FOUND", "消息任务不存在。")
        AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
        actor, bad = require_live(request, "notification.recover")
        if bad:
            return bad
        _, bad = require_live(request, "notification.read")
        if bad:
            return bad
        limited = _recovery_limit(request, actor)
        if limited:
            return limited
        if (task.updated_at.isoformat() != body["expectedUpdatedAt"] or
                task.attempt_count != body["expectedAttemptCount"]):
            return error(request, 409, "REVISION_CONFLICT", "任务已变化，请刷新后重试。")
        if (task.status != "RESERVED" or task.lease_until is None or
                task.lease_until > timezone.now() or task.has_lease_attempt):
            return error(request, 409, "RECOVERY_NOT_SAFE", "只有过期且未开始发送的预约可恢复；请刷新任务状态。")
        bad = confirm_action(request, actor, RECOVERY_ACTION, task_id, task.attempt_count)
        if bad:
            return bad
        before = {"status": task.status, "attemptCount": task.attempt_count,
                  "leaseUntil": _timestamp(task.lease_until)}
        task.status = "READY"
        task.reason_code = ""
        task.lease_token = None
        task.lease_until = None
        task.save(update_fields=["status", "reason_code", "lease_token", "lease_until", "updated_at"])
        audit(request, RECOVERY_ACTION, "subscription_message_task", task.pk, actor,
              before=before, after={"status": task.status, "attemptCount": task.attempt_count})
    task.has_lease_attempt = False
    return response(request, _summary(task, timezone.now()))
