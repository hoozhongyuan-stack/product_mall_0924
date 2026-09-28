"""Bounded, fenced CSV generation outside the HTTP request transaction."""

import csv
import hashlib
import io
import json
import logging
import os
import shutil
import unicodedata
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounts.audit_read import audit_entry, audit_queryset
from accounts.read_window import read_window
from accounts.security import operation_permissions
from catalog.storage import LocalStorage
from catalog.validation import CatalogError
from payments.business_read import business_summary

from .models import ExportTask


LOGGER = logging.getLogger(__name__)
LEASE_DURATION = timedelta(minutes=30)
MAX_AUDIT_ROWS = 20_000
MAX_ATTEMPTS = 3
CAPACITY_LOCK = 759421836640912


class ExportFailure(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def safe_csv_cell(value):
    """Keep trusted numbers numeric; make any spreadsheet formula text inert."""
    if not isinstance(value, str):
        return "" if value is None else value
    visible = value
    while visible and (visible[0].isspace() or unicodedata.category(visible[0]) in {"Cc", "Cf"}):
        visible = visible[1:]
    return "'" + value if visible.startswith(("=", "+", "-", "@")) else value


class CsvSink:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.bytes = 0
        self.rows = 0
        self.max_bytes = min(settings.STORAGE_EXPORT_MAX_BYTES, 100 * 1024 * 1024)
        self._write(b"\xef\xbb\xbf")

    def _write(self, data):
        if self.bytes + len(data) > self.max_bytes:
            raise ExportFailure("FILE_TOO_LARGE")
        self.stream.write(data)
        self.digest.update(data)
        self.bytes += len(data)

    def row(self, cells, *, count=True):
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerow([safe_csv_cell(value) for value in cells])
        self._write(buffer.getvalue().encode("utf-8"))
        if count:
            self.rows += 1


def _audit_csv(sink, task, start, end):
    sink.row(["时间", "操作者ID", "动作", "对象类型", "对象ID", "结果", "请求ID", "变更前", "变更后"], count=False)
    filters = task.filters
    rows = audit_queryset(start, end, filters.get("actorId", ""), filters.get("actionCode", ""),
                          filters.get("objectType", ""), filters.get("objectId", ""),
                          filters.get("result", ""))
    rows = rows.filter(occurred_at__lte=task.created_at).order_by("occurred_at", "id")[:MAX_AUDIT_ROWS + 1]
    for row in rows.iterator(chunk_size=200):
        if sink.rows >= MAX_AUDIT_ROWS:
            raise ExportFailure("ROW_LIMIT")
        item = audit_entry(row)
        sink.row([item["occurredAt"], item["actorId"], item["actionCode"], item["objectType"],
                  item["objectId"], item["result"], item["requestId"],
                  json.dumps(item["before"], ensure_ascii=False, separators=(",", ":")),
                  json.dumps(item["after"], ensure_ascii=False, separators=(",", ":"))])


def _business_csv(sink, first, last, start, end):
    sink.row(["日期", "现金支付笔数", "支付金额(分)", "积分兑换笔数", "退款笔数", "退款金额(分)", "净成交(分)"], count=False)
    summary = business_summary(first, last, start, end)
    for day in summary["days"]:
        sink.row([day["date"], day["paidOrderCount"], day["paidAmountFen"],
                  day["pointsExchangeCount"], day["refundCount"], day["refundAmountFen"],
                  day["netAmountFen"]])


def _render(task, storage):
    first, last, start, end = read_window(task.filters)
    temporary = storage.prepare_parent(f"tmp/export-{task.id.hex}-{task.lease_token.hex}.upload")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            sink = CsvSink(stream)
            if task.kind == ExportTask.Kind.AUDIT:
                _audit_csv(sink, task, start, end)
            elif task.kind == ExportTask.Kind.BUSINESS:
                _business_csv(sink, first, last, start, end)
            else:
                raise ExportFailure("INVALID_KIND")
            stream.flush()
            os.fsync(stream.fileno())
        return temporary, sink.bytes, sink.digest.hexdigest(), sink.rows
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def claim_next(*, now=None):
    now = now or timezone.now()
    while True:
        with transaction.atomic():
            task = (ExportTask.objects.select_related("created_by").select_for_update(skip_locked=True)
                    .filter(Q(status=ExportTask.Status.PENDING) |
                            Q(status=ExportTask.Status.RUNNING, lease_until__lte=now))
                    .order_by("created_at", "id").first())
            if task is None:
                return None
            if task.attempt_count >= MAX_ATTEMPTS:
                task.status = ExportTask.Status.FAILED
                task.failure_code = "INTERRUPTED"
                task.lease_token = task.lease_until = None
                task.completed_at = now
                task.save(update_fields=["status", "failure_code", "lease_token", "lease_until",
                                         "completed_at", "updated_at"])
                continue
            required = ({"audit.read", "audit.export"} if task.kind == ExportTask.Kind.AUDIT else
                        {"business.report.read", "business.report.export"})
            if not required <= set(operation_permissions(task.created_by)):
                task.status = ExportTask.Status.FAILED
                task.failure_code = "PERMISSION_REVOKED"
                task.lease_token = task.lease_until = None
                task.completed_at = now
                task.save(update_fields=["status", "failure_code", "lease_token", "lease_until",
                                         "completed_at", "updated_at"])
                return task
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CAPACITY_LOCK])
            file_cap = min(settings.STORAGE_EXPORT_MAX_BYTES, 100 * 1024 * 1024)
            ready_bytes = (ExportTask.objects.filter(status=ExportTask.Status.READY)
                           .aggregate(total=Sum("file_bytes"))["total"] or 0)
            active_count = ExportTask.objects.filter(status=ExportTask.Status.RUNNING,
                                                     lease_until__gt=now).count()
            storage = LocalStorage()
            try:
                storage.prepare_parent("export/placeholder")
                free_bytes = shutil.disk_usage(storage.root).free
            except (OSError, CatalogError):
                task.status = ExportTask.Status.FAILED
                task.failure_code = "STORAGE_UNAVAILABLE"
                task.lease_token = task.lease_until = None
                task.completed_at = now
                task.save(update_fields=["status", "failure_code", "lease_token", "lease_until",
                                         "completed_at", "updated_at"])
                return task
            enough_quota = ready_bytes + (active_count + 1) * file_cap <= settings.STORAGE_EXPORT_GLOBAL_MAX_BYTES
            enough_disk = free_bytes >= (active_count + 1) * file_cap + settings.STORAGE_EXPORT_MIN_FREE_BYTES
            if not enough_quota or not enough_disk:
                task.status = ExportTask.Status.FAILED
                task.failure_code = "CAPACITY_LIMIT" if not enough_quota else "DISK_LOW"
                task.lease_token = task.lease_until = None
                task.completed_at = now
                task.save(update_fields=["status", "failure_code", "lease_token", "lease_until",
                                         "completed_at", "updated_at"])
                return task
            task.status = ExportTask.Status.RUNNING
            task.attempt_count += 1
            task.lease_token = uuid.uuid4()
            task.lease_until = now + LEASE_DURATION
            task.failure_code = ""
            task.save(update_fields=["status", "attempt_count", "lease_token", "lease_until",
                                     "failure_code", "updated_at"])
            return task


def _commit_file(task, temporary, size, digest, row_count, storage):
    with transaction.atomic():
        live = ExportTask.objects.select_for_update().get(pk=task.pk)
        now = timezone.now()
        if (live.status != ExportTask.Status.RUNNING or live.lease_token != task.lease_token or
                live.lease_until is None or live.lease_until <= now):
            raise ExportFailure("LEASE_LOST")
        key = storage.key("export", f"{task.id}.csv")
        destination = storage.path(key)
        # A previous interrupted installation is unreachable because the row is not READY.
        if destination.exists():
            destination.unlink()
            storage.sync_directory(destination.parent)
        storage.promote(temporary, key)
        live.status = ExportTask.Status.READY
        live.lease_token = live.lease_until = None
        live.object_key = key
        live.file_sha256 = digest
        live.file_bytes = size
        live.row_count = row_count
        live.completed_at = now
        live.expires_at = now + timedelta(hours=24)
        live.save(update_fields=["status", "lease_token", "lease_until", "object_key",
                                 "file_sha256", "file_bytes", "row_count", "completed_at",
                                 "expires_at", "updated_at"])


def _fail(task, code):
    with transaction.atomic():
        live = ExportTask.objects.select_for_update().get(pk=task.pk)
        if live.status != ExportTask.Status.RUNNING or live.lease_token != task.lease_token:
            return
        live.status = ExportTask.Status.FAILED
        live.failure_code = code
        live.lease_token = live.lease_until = None
        live.completed_at = timezone.now()
        live.save(update_fields=["status", "failure_code", "lease_token", "lease_until",
                                 "completed_at", "updated_at"])


def run_next():
    """Return task ID after one attempt, or None when the queue is empty."""
    task = claim_next()
    if task is None:
        return None
    if task.status != ExportTask.Status.RUNNING:
        return task.pk
    storage = LocalStorage()
    temporary = None
    try:
        temporary, size, digest, rows = _render(task, storage)
        _commit_file(task, temporary, size, digest, rows, storage)
    except ExportFailure as exc:
        LOGGER.warning("Export task %s failed: %s", task.pk, exc.code)
        _fail(task, exc.code)
    except Exception:
        LOGGER.exception("Export task %s failed", task.pk)
        _fail(task, "GENERATION_FAILED")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return task.pk
