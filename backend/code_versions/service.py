"""Deployment-only build and private local storage sync.

This service never calls WeChat. A READY version proves only that the source
snapshot was installed under the private persistent code/ namespace.
"""

import hashlib
import logging
import os

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from catalog.storage import LocalStorage, lock_storage
from catalog.validation import CatalogError

from .models import CodeBuildJob, CodeSourceProvenance, CodeVersion
from .package import PackageError, build_package
from .trusted_source import REVISION, build_git_package
from wechat_integration.credentials import CredentialsUnavailable, effective_app_id


LOGGER = logging.getLogger(__name__)
BUILD_LOCK = 72988422199281


class CodeBuildError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def object_status(version, *, verify_digest=False):
    """Current private-object read state; never exposes the storage key."""
    try:
        path = LocalStorage().path(version.object_key)
        if not path.is_file() or path.stat().st_size != version.package_bytes:
            return "UNAVAILABLE"
        if verify_digest:
            return "READY" if _digest(path) == version.package_sha256 else "UNAVAILABLE"
        return "STORED_UNVERIFIED"
    except (OSError, CatalogError):
        return "UNAVAILABLE"


def _verify_existing(storage, key, package):
    path = storage.path(key)
    if not path.is_file():
        return False
    if path.stat().st_size != len(package.data) or _digest(path) != package.package_sha256:
        raise CodeBuildError("STORAGE_INTEGRITY")
    return True


def _install(storage, package, key):
    if _verify_existing(storage, key, package):
        return
    temporary = storage.temporary()
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(package.data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            storage.promote(temporary, key)
        except FileExistsError:
            # A prior transaction can leave the same content-addressed object.
            if not _verify_existing(storage, key, package):
                raise CodeBuildError("STORAGE_INTEGRITY")
        if not _verify_existing(storage, key, package):
            raise CodeBuildError("STORAGE_INTEGRITY")
    finally:
        temporary.unlink(missing_ok=True)


def _record_failure(job, code):
    with transaction.atomic():
        CodeBuildJob.objects.filter(pk=job.pk, status="STARTED").update(
            status="FAILED", failure_code=code, completed_at=timezone.now())
    LOGGER.warning("Mini Program local source snapshot failed: %s", code)


def _start_job(source_revision=""):
    with transaction.atomic():
        CodeBuildJob.objects.filter(status="STARTED").update(
            status="FAILED", failure_code="INTERRUPTED", completed_at=timezone.now())
        return CodeBuildJob.objects.create(source_revision=source_revision)


def _store_version(job, package, source_revision=""):
    storage = LocalStorage()
    key = storage.key("code", f"source-{package.package_sha256}.zip")
    with transaction.atomic():
        lock_storage()
        existing = CodeVersion.objects.filter(source_digest=package.source_digest).first()
        if existing and (existing.package_sha256 != package.package_sha256 or existing.object_key != key):
            raise CodeBuildError("VERSION_CONFLICT")
        _install(storage, package, key)
        if existing:
            version = existing
        else:
            version = CodeVersion.objects.create(
                version_label=f"source-{package.source_digest[:12]}",
                source_revision="", source_digest=package.source_digest,
                package_sha256=package.package_sha256, package_bytes=len(package.data),
                file_count=package.file_count, object_key=key, storage_status="READY",
                platform_status="NOT_CONFIGURED")
        if source_revision:
            # A previous local snapshot may have identical bytes but no proven
            # origin. Keep that immutable version and attach a separate fact.
            proof = CodeSourceProvenance.objects.filter(source_revision=source_revision).first()
            if proof and proof.version_id != version.pk:
                raise CodeBuildError("VERSION_CONFLICT")
            if not proof:
                CodeSourceProvenance.objects.create(version=version,
                                                    source_revision=source_revision)
        job.version = version
        job.status = "SUCCEEDED"
        job.source_revision = source_revision
        job.completed_at = timezone.now()
        job.save(update_fields=["version", "status", "source_revision", "completed_at"])
        return version


def _run_build(package_builder, source_revision=""):
    """Persist STARTED before I/O; success, proof and version commit together."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [BUILD_LOCK])
    try:
        requested_revision = (source_revision if isinstance(source_revision, str) and
                              REVISION.fullmatch(source_revision) else "")
        job = _start_job(requested_revision)
        try:
            package = package_builder()
            return _store_version(job, package, source_revision)
        except (PackageError, CodeBuildError) as exc:
            _record_failure(job, exc.code)
            raise
        except (OSError, CatalogError) as exc:
            _record_failure(job, "STORAGE_UNAVAILABLE")
            raise CodeBuildError("STORAGE_UNAVAILABLE") from exc
        except DatabaseError as exc:
            # A committed STARTED row survives the failed transaction. Record
            # an explicit failure if possible; otherwise next run fences it.
            try:
                _record_failure(job, "DATABASE_UNAVAILABLE")
            except DatabaseError:
                LOGGER.error("Mini Program build job remains STARTED after database failure")
            raise CodeBuildError("DATABASE_UNAVAILABLE") from exc
    except DatabaseError as exc:
        raise CodeBuildError("DATABASE_UNAVAILABLE") from exc
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [BUILD_LOCK])
        except DatabaseError:
            # The connection has gone away; PostgreSQL released its session lock.
            LOGGER.error("Mini Program build database connection was lost")


def build_local_version():
    """Build one local snapshot without asserting a Git revision."""
    return _run_build(lambda: build_package(settings.MINIPROGRAM_SOURCE_ROOT,
                                           settings.STORAGE_CODE_MAX_BYTES))


def build_trusted_version(repo_root, expected_revision):
    """Build from Git objects after proving the checkout matches expected HEAD."""
    def package_builder():
        api_base_url = settings.MALL_MINIPROGRAM_API_BASE_URL
        if not api_base_url:
            return build_git_package(repo_root, expected_revision, settings.STORAGE_CODE_MAX_BYTES)
        try:
            app_id = effective_app_id()
        except CredentialsUnavailable as exc:
            raise PackageError('RELEASE_CONFIG_INVALID') from exc
        return build_git_package(repo_root, expected_revision, settings.STORAGE_CODE_MAX_BYTES,
                                 release_app_id=app_id, api_base_url=api_base_url)

    return _run_build(package_builder,
                      expected_revision)
