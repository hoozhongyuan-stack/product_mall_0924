"""Retire private exports after 24 hours and reconcile unreachable files."""

import logging
import re
import uuid

from django.db import transaction
from django.utils import timezone

from catalog.storage import LocalStorage
from catalog.validation import CatalogError

from .models import ExportTask


LOGGER = logging.getLogger(__name__)
EXPORT_TEMPORARY = re.compile(r"export-([0-9a-f]{32})-([0-9a-f]{32})\.upload\Z")


def _delete(path, storage):
    if not path.exists():
        return False
    path.unlink()
    storage.sync_directory(path.parent)
    return True


def purge_expired_exports(*, limit=100, now=None):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    now = now or timezone.now()
    storage = LocalStorage()
    expired = removed = temporary_removed = 0
    due = list(ExportTask.objects.filter(status=ExportTask.Status.READY, expires_at__lte=now)
               .order_by("expires_at", "id").values_list("pk", flat=True)[:limit])
    for task_id in due:
        try:
            with transaction.atomic():
                task = ExportTask.objects.select_for_update().get(pk=task_id)
                if task.status != ExportTask.Status.READY or task.expires_at > now:
                    continue
                expected_key = storage.key("export", f"{task.id}.csv")
                if task.object_key != expected_key:
                    LOGGER.error("Export task %s has an unexpected storage identity", task_id)
                    continue
                if _delete(storage.path(expected_key), storage):
                    removed += 1
                task.status = ExportTask.Status.EXPIRED
                task.save(update_fields=["status", "updated_at"])
                expired += 1
        except (OSError, CatalogError):
            LOGGER.exception("Export cleanup remains pending for task %s", task_id)

    try:
        export_root = storage.path("export/placeholder").parent
        paths = sorted(export_root.glob("*.csv")) if export_root.exists() else []
        for candidate in paths:
            if removed >= limit:
                break
            try:
                identifier = uuid.UUID(candidate.stem)
            except ValueError:
                LOGGER.error("Unexpected file in private export namespace: %s", candidate.name)
                continue
            try:
                with transaction.atomic():
                    task = ExportTask.objects.select_for_update().filter(pk=identifier).first()
                    if task and ((task.status == ExportTask.Status.READY and task.expires_at > now) or
                                 (task.status == ExportTask.Status.RUNNING and task.lease_until > now)):
                        continue
                    key = storage.key("export", f"{identifier}.csv")
                    if _delete(storage.path(key), storage):
                        removed += 1
                    if task and task.status == ExportTask.Status.READY and task.expires_at <= now:
                        task.status = ExportTask.Status.EXPIRED
                        task.save(update_fields=["status", "updated_at"])
                        expired += 1
            except (OSError, CatalogError):
                LOGGER.exception("Export orphan cleanup remains pending for %s", identifier)
    except (OSError, CatalogError):
        LOGGER.exception("Private export namespace unavailable during cleanup")
    try:
        temporary_root = storage.path("tmp/placeholder").parent
        temporaries = sorted(temporary_root.glob("export-*.upload")) if temporary_root.exists() else []
        for candidate in temporaries:
            if temporary_removed >= limit:
                break
            match = EXPORT_TEMPORARY.fullmatch(candidate.name)
            if match is None:
                LOGGER.error("Unexpected export temporary name: %s", candidate.name)
                continue
            task_id, lease_token = uuid.UUID(hex=match[1]), uuid.UUID(hex=match[2])
            try:
                with transaction.atomic():
                    task = ExportTask.objects.select_for_update().filter(pk=task_id).first()
                    if task and task.status == ExportTask.Status.RUNNING and task.lease_token == lease_token and \
                            task.lease_until > now:
                        continue
                    key = f"tmp/{candidate.name}"
                    if _delete(storage.path(key), storage):
                        temporary_removed += 1
            except (OSError, CatalogError):
                LOGGER.exception("Export temporary cleanup remains pending for task %s", task_id)
    except (OSError, CatalogError):
        LOGGER.exception("Private export temporary namespace unavailable during cleanup")
    return {"expiredTasks": expired, "removedFiles": removed,
            "removedTemporaryFiles": temporary_removed}
