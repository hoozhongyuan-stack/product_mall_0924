"""Owner-scoped export requests and verified private downloads."""

import hashlib
import os
import re
import stat
import uuid
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie

from accounts.models import AdminAccount
from accounts.read_rate_limit import read_rate_limit
from accounts.security import audit, error, parse_json, permissions, require, require_live, response
from accounts.views import method
from catalog.storage import LocalStorage
from catalog.validation import CatalogError

from .filters import filter_digest, normalize_filters
from .models import ExportTask


CURSOR_SALT = "private-export-list-v1"
MAX_REQUESTS_PER_HOUR = 10
MAX_ACTIVE_REQUESTS = 3


def _timestamp(value):
    return value.isoformat() if value else None


def _codes(kind):
    return ("audit.read", "audit.export") if kind == ExportTask.Kind.AUDIT else (
        "business.report.read", "business.report.export")


def _granted_kinds(actor):
    granted = set(permissions(actor))
    return [kind for kind in ExportTask.Kind.values if set(_codes(kind)) <= granted]


def _authorize(request, kind, *, live=False):
    checker = require_live if live else require
    actor = None
    for code in _codes(kind):
        actor, bad = checker(request, code)
        if bad:
            return None, bad
    return actor, None


def _task_item(task):
    now = timezone.now()
    status = "EXPIRED" if task.expires_at and task.expires_at <= now else task.status
    return {"taskId": str(task.pk), "requestKey": str(task.request_key),
            "kind": task.kind, "filters": task.filters,
            "status": status, "attemptCount": task.attempt_count,
            "rowCount": task.row_count if task.status == "READY" else None,
            "fileBytes": task.file_bytes if task.status == "READY" else None,
            "failureCode": task.failure_code or None, "createdAt": _timestamp(task.created_at),
            "updatedAt": _timestamp(task.updated_at), "completedAt": _timestamp(task.completed_at),
            "expiresAt": _timestamp(task.expires_at)}


def _create(request):
    try:
        body = parse_json(request)
        if set(body) != {"kind", "filters", "requestKey"} or body["kind"] not in ExportTask.Kind.values:
            raise ValueError("导出请求参数不正确。")
        request_key = uuid.UUID(body["requestKey"])
        kind = body["kind"]
        normalized = normalize_filters(kind, body["filters"])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return error(request, 400, "VALIDATION_FAILED", "导出请求参数不正确。")
    actor, bad = _authorize(request, kind)
    if bad:
        return bad
    digest = filter_digest(kind, normalized)
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor, bad = _authorize(request, kind, live=True)
        if bad:
            return bad
        existing = ExportTask.objects.filter(created_by=actor, request_key=request_key).first()
        if existing:
            if existing.kind != kind or existing.filters_digest != digest:
                return error(request, 409, "REQUEST_CONFLICT", "请求标识已用于其他导出条件。")
            return response(request, _task_item(existing))
        limited = read_rate_limit(request, actor, "audit" if kind == "AUDIT" else "business")
        if limited:
            return limited
        since = timezone.now() - timedelta(hours=1)
        if ExportTask.objects.filter(created_by=actor, created_at__gte=since).count() >= MAX_REQUESTS_PER_HOUR:
            limited = error(request, 429, "RATE_LIMITED", "过去一小时的导出次数已达上限。")
            limited["Retry-After"] = "3600"
            return limited
        if ExportTask.objects.filter(created_by=actor, status__in=["PENDING", "RUNNING"]).count() >= MAX_ACTIVE_REQUESTS:
            return error(request, 429, "RATE_LIMITED", "待处理导出任务过多，请稍后重试。")
        task = ExportTask.objects.create(kind=kind, created_by=actor, request_key=request_key,
                                         filters=normalized, filters_digest=digest)
        audit(request, "export.create", "export_task", task.pk, actor,
              after={"kind": kind, "status": task.status})
    return response(request, _task_item(task), 201)


def _cursor_position(token, actor_id, kind):
    try:
        payload = signing.loads(token, salt=CURSOR_SALT, max_age=3600)
        if not isinstance(payload, dict) or set(payload) != {"actor", "kind", "at", "id"} or \
                payload["actor"] != str(actor_id) or payload["kind"] != kind:
            raise ValueError
        at = parse_datetime(payload["at"])
        identifier = uuid.UUID(payload["id"])
        if at is None or not timezone.is_aware(at):
            raise ValueError
        return at, identifier
    except (ValueError, TypeError, KeyError, signing.BadSignature) as exc:
        raise ValueError("游标已失效，请重新查询。") from exc


def _list(request):
    actor, bad = require(request)
    if bad:
        return bad
    params = request.GET
    if set(params) - {"kind", "limit", "cursor"} or any(len(params.getlist(key)) != 1 for key in params):
        return error(request, 400, "VALIDATION_FAILED", "筛选参数不正确。")
    kind = params.get("kind", "")
    raw_limit = params.get("limit", "20")
    if kind and kind not in ExportTask.Kind.values or not re.fullmatch(r"[1-9][0-9]*", raw_limit) or \
            int(raw_limit) > 100 or len(params.get("cursor", "")) > 2048:
        return error(request, 400, "VALIDATION_FAILED", "筛选参数不正确。")
    allowed = _granted_kinds(actor)
    if kind and kind not in allowed or not allowed:
        return error(request, 403, "PERMISSION_DENIED", "当前账号没有导出查询权限。")
    try:
        position = _cursor_position(params["cursor"], actor.pk, kind) if params.get("cursor") else None
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    limited = read_rate_limit(request, actor, "business" if kind == "BUSINESS" or
                              "AUDIT" not in allowed else "audit")
    if limited:
        return limited
    rows = ExportTask.objects.filter(created_by=actor, kind__in=[kind] if kind else allowed)
    if position:
        rows = rows.filter(Q(created_at__lt=position[0]) |
                           Q(created_at=position[0], id__lt=position[1]))
    limit = int(raw_limit)
    rows = list(rows.order_by("-created_at", "-id")[:limit + 1])
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = signing.dumps({"actor": str(actor.pk), "kind": kind,
                                     "at": last.created_at.isoformat(), "id": str(last.pk)}, salt=CURSOR_SALT)
    return response(request, {"items": [_task_item(row) for row in rows[:limit]], "nextCursor": next_cursor})


@never_cache
@vary_on_cookie
def exports_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    return _create(request) if request.method == "POST" else _list(request)


def _owned_task(request, task_id):
    actor, bad = require(request)
    if bad:
        return None, bad
    task = ExportTask.objects.filter(pk=task_id, created_by=actor).first()
    if not task:
        return None, error(request, 404, "NOT_FOUND", "导出任务不存在。")
    _, bad = _authorize(request, task.kind)
    return task, bad


@never_cache
@vary_on_cookie
def export_detail_view(request, task_id):
    bad = method(request, "GET")
    if bad:
        return bad
    task, bad = _owned_task(request, task_id)
    if bad:
        return bad
    limited = read_rate_limit(request, task.created_by, "audit" if task.kind == "AUDIT" else "business")
    return limited or response(request, _task_item(task))


def _verified_file(task):
    expected_key = f"export/{task.pk}.csv"
    if (task.object_key != expected_key or not re.fullmatch(r"[0-9a-f]{64}", task.file_sha256) or
            not 0 < task.file_bytes <= settings.STORAGE_EXPORT_MAX_BYTES):
        raise OSError("export metadata invalid")
    path = LocalStorage().path(expected_key)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    stream = os.fdopen(descriptor, "rb")
    try:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != task.file_bytes:
            raise OSError("export size mismatch")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        if digest.hexdigest() != task.file_sha256:
            raise OSError("export digest mismatch")
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise


@never_cache
@vary_on_cookie
def export_download_view(request, task_id):
    bad = method(request, "GET")
    if bad:
        return bad
    task, bad = _owned_task(request, task_id)
    if bad:
        return bad
    if task.status == "EXPIRED" or task.expires_at and task.expires_at <= timezone.now():
        return error(request, 410, "EXPORT_EXPIRED", "导出文件已过期。")
    if task.status != "READY":
        return error(request, 409, "EXPORT_NOT_READY", "导出文件尚未生成。")
    limited = read_rate_limit(request, task.created_by, "audit" if task.kind == "AUDIT" else "business")
    if limited:
        return limited
    try:
        stream = _verified_file(task)
    except (OSError, CatalogError):
        return error(request, 503, "EXPORT_STORAGE_UNAVAILABLE", "导出文件暂不可用，请稍后重试。")
    try:
        with transaction.atomic():
            AdminAccount.objects.select_for_update().get(pk=task.created_by_id)
            actor, bad = require_live(request)
            if bad:
                stream.close()
                return bad
            fresh = ExportTask.objects.filter(pk=task_id, created_by=actor).first()
            if fresh is None:
                stream.close()
                return error(request, 404, "NOT_FOUND", "导出任务不存在。")
            _, bad = _authorize(request, fresh.kind, live=True)
            if bad:
                stream.close()
                return bad
            if fresh.status == "EXPIRED" or fresh.expires_at and fresh.expires_at <= timezone.now():
                stream.close()
                return error(request, 410, "EXPORT_EXPIRED", "导出文件已过期。")
            if (fresh.status != "READY" or fresh.kind != task.kind or
                    fresh.object_key != task.object_key or fresh.file_sha256 != task.file_sha256 or
                    fresh.file_bytes != task.file_bytes):
                stream.close()
                return error(request, 409, "EXPORT_NOT_READY", "导出文件尚未生成。")
            audit(request, "export.download.open", "export_task", task.pk, actor,
                  after={"kind": task.kind, "status": task.status})
    except Exception:
        stream.close()
        raise
    filename = f"{task.kind.lower()}-{task.pk}.csv"
    result = FileResponse(stream, as_attachment=True, filename=filename, content_type="text/csv; charset=utf-8")
    result["Cache-Control"] = "private, no-store"
    result["X-Content-Type-Options"] = "nosniff"
    return result
