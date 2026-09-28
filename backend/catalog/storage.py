"""Private local storage contract with durable, recoverable deletion intents.

Files live under the deployment-owned persistent MEDIA_ROOT. New objects use
media/, code/, export/ namespaces; historical product/startup/page keys remain
readable. Each namespace has its own consumer and retention policy.
"""
import json
import logging
import os
import uuid
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from .validation import CatalogError

LOGGER = logging.getLogger(__name__)
STORAGE_LOCK = 628940218453216
LEGACY_NAMESPACES = frozenset({"product", "startup", "page"})
NAMESPACES = frozenset({"media", "code", "export"})
INTERNAL_NAMESPACES = frozenset({"tmp", ".pending-delete"})


def storage_unavailable(exc):
    return CatalogError("素材存储暂不可用，请稍后重试。", "MEDIA_STORAGE_UNAVAILABLE", 503)


def lock_storage():
    """Serialize file intents, orphan quotas and recovery on PostgreSQL."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [STORAGE_LOCK])


class LocalStorage:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else settings.MEDIA_ROOT).absolute()

    def policy(self, namespace):
        if namespace not in NAMESPACES:
            raise CatalogError("存储命名空间无效。", "MEDIA_INVALID")
        return {"namespace": namespace, "visibility": "private",
                "immutable": namespace == "code",
                "retentionHours": 24 if namespace == "export" else None,
                "maximumBytes": {"media": settings.PRODUCT_VIDEO_MAX_BYTES,
                                 "code": settings.STORAGE_CODE_MAX_BYTES,
                                 "export": settings.STORAGE_EXPORT_MAX_BYTES}[namespace]}

    def key(self, namespace, name):
        self.policy(namespace)
        key = f"{namespace}/{name}"
        self.path(key)
        return key

    def path(self, key):
        if not isinstance(key, str) or "\\" in key or "\x00" in key:
            raise CatalogError("素材存储路径无效。", "MEDIA_INVALID")
        parts = key.split("/")
        if len(parts) < 2 or any(part in ("", ".", "..") for part in parts) or \
                parts[0] not in NAMESPACES | LEGACY_NAMESPACES | INTERNAL_NAMESPACES:
            raise CatalogError("素材存储路径无效。", "MEDIA_INVALID")
        relative = PurePosixPath(key)
        if relative.is_absolute():
            raise CatalogError("素材存储路径无效。", "MEDIA_INVALID")
        candidate = self.root
        if self.root.is_symlink():
            raise CatalogError("素材存储路径无效。", "MEDIA_INVALID")
        for component in parts:
            candidate = candidate / component
            if candidate.is_symlink():
                raise CatalogError("素材存储路径无效。", "MEDIA_INVALID")
        return candidate

    def prepare_parent(self, key):
        path = self.path(key)
        missing = []
        cursor = path.parent
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
        # Validate again after creation, preventing accidental symlink traversal.
        return self.path(key)

    def promote(self, temporary, key):
        """Atomically install a unique object without replacing an existing key."""
        target = self.prepare_parent(key)
        # All temporary and target files share the persistent storage filesystem.
        os.link(temporary, target)
        temporary.unlink()
        self.sync_directory(target.parent)
        return target

    @staticmethod
    def sync_directory(directory):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def temporary(self):
        return self.prepare_parent(f"tmp/{uuid.uuid4().hex}.upload")

    def stage_intent(self, asset_id, key):
        self.path(key)
        marker_key = f".pending-delete/{uuid.uuid4().hex}.json"
        marker = self.prepare_parent(marker_key)
        try:
            with marker.open("x", encoding="utf-8") as stream:
                os.chmod(marker, 0o600)
                json.dump({"assetId": str(asset_id), "key": key}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            self.sync_directory(marker.parent)
        except Exception:
            marker.unlink(missing_ok=True)
            raise
        return marker_key

    def finish_intent(self, marker_key):
        """Run after commit; keep failed intents for a later recovery pass."""
        try:
            with transaction.atomic():
                lock_storage()
                self._finish_intent(marker_key)
        except (OSError, DatabaseError, CatalogError, ValueError, KeyError, json.JSONDecodeError):
            LOGGER.warning("Storage cleanup remains pending for %s", marker_key)

    def _finish_intent(self, marker_key):
        from .models import Asset
        marker = self.path(marker_key)
        if not marker.exists():
            return
        payload = json.loads(marker.read_text(encoding="utf-8"))
        identifier = uuid.UUID(payload["assetId"])
        key = payload["key"]
        if not isinstance(key, str) or key.split("/")[0] not in LEGACY_NAMESPACES | {"media"}:
            raise CatalogError("素材清理标记无效。", "MEDIA_INVALID")
        # A corrupt intent must not delete a different asset's retained file.
        if Asset.objects.filter(stored_name=key).exclude(pk=identifier).exists():
            raise CatalogError("素材清理标记与文件引用不一致。", "MEDIA_INVALID")
        # A rolled-back deletion / committed upload must retain its file.
        if not Asset.objects.filter(pk=identifier).exists():
            target = self.path(key)
            target.unlink(missing_ok=True)
            if target.parent.exists():
                self.sync_directory(target.parent)
        marker.unlink(missing_ok=True)
        self.sync_directory(marker.parent)

    def discard(self, key):
        self.path(key).unlink(missing_ok=True)


def recover_media_storage():
    """Resume durable intents and remove only expired upload temporaries.

    The shared transaction lock excludes active uploads/deletions, so recovery
    never mistakes their uncommitted rows for abandoned files.
    """
    storage = LocalStorage()
    recovered = temporary_removed = 0
    outer_transaction = connection.in_atomic_block
    try:
        with transaction.atomic():
            lock_storage()
            pending = storage.path(".pending-delete/placeholder").parent
            for marker in sorted(pending.glob("*.json")):
                marker_key = f".pending-delete/{marker.name}"
                if outer_transaction:
                    # This connection may see its own uncommitted upload/delete.
                    # Reconciliation must wait for the caller's actual commit.
                    transaction.on_commit(lambda key=marker_key: storage.finish_intent(key))
                    continue
                try:
                    storage._finish_intent(marker_key)
                    recovered += 1
                except (CatalogError, ValueError, KeyError, TypeError):
                    LOGGER.error("Invalid storage cleanup marker retained: %s", marker.name)
            temporary_root = storage.path("tmp/placeholder").parent
            cutoff = (timezone.now() - settings.STORAGE_TEMPORARY_TTL).timestamp()
            for temporary in temporary_root.glob("*.upload"):
                path = storage.path(f"tmp/{temporary.name}")
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    temporary_removed += 1
    except OSError as exc:
        raise storage_unavailable(exc) from exc
    return {"recoveredIntents": recovered, "temporaryFilesRemoved": temporary_removed}
