"""Permission-gated, non-downloadable local code version read model."""

import uuid

from django.core import signing
from django.db.models import OuterRef, Q, Subquery
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache

from accounts.security import error, require, response
from accounts.views import method

from .models import CodeBuildJob, CodeSourceProvenance, CodeVersion
from .service import object_status


def _page(request, query, salt, serializer):
    if set(request.GET) - {"limit", "cursor"}:
        return None, error(request, 400, "VALIDATION_FAILED", "查询参数不正确。")
    try:
        limit = int(request.GET.get("limit", "20"))
    except ValueError:
        return None, error(request, 400, "VALIDATION_FAILED", "分页数量不正确。")
    if not 1 <= limit <= 100:
        return None, error(request, 400, "VALIDATION_FAILED", "分页数量不正确。")
    cursor = request.GET.get("cursor")
    if cursor:
        try:
            if len(cursor) > 4096:
                raise ValueError
            position = signing.loads(cursor, salt=salt, max_age=86400)
            if not isinstance(position, dict) or set(position) != {"at", "id"}:
                raise ValueError
            timestamp = parse_datetime(position["at"])
            identifier = uuid.UUID(position["id"])
            if timestamp is None or timestamp.utcoffset() is None:
                raise ValueError
        except (signing.BadSignature, ValueError, TypeError, AttributeError):
            return None, error(request, 400, "VALIDATION_FAILED", "分页游标不正确或已过期。")
        query = query.filter(Q(created_at__lt=timestamp) |
                             Q(created_at=timestamp, id__lt=identifier))
    rows = list(query.order_by("-created_at", "-id")[:limit + 1])
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = signing.dumps({"at": last.created_at.isoformat(), "id": str(last.pk)}, salt=salt)
    return {"items": [serializer(row) for row in rows[:limit]], "nextCursor": next_cursor}, None


def _version(version, *, verify_digest=False):
    return {"versionId": str(version.pk), "versionLabel": version.version_label,
            "sourceRevision": version.proven_revision,
            "sourceDigest": version.source_digest, "packageSha256": version.package_sha256,
            "packageBytes": version.package_bytes, "fileCount": version.file_count,
            "storageStatus": object_status(version, verify_digest=verify_digest),
            "platformStatus": version.platform_status,
            "createdAt": version.created_at.isoformat(),
            "completedAt": version.completed_at.isoformat(), "failureCode": ""}


def _job(job):
    return {"taskId": str(job.pk), "versionId": str(job.version_id) if job.version_id else None,
            "status": job.status, "failureCode": job.failure_code,
            "sourceRevision": job.source_revision or None,
            "createdAt": job.created_at.isoformat(),
            "completedAt": job.completed_at.isoformat() if job.completed_at else None}


def _versions():
    newest = (CodeSourceProvenance.objects.filter(version_id=OuterRef("pk"))
              .order_by("-verified_at", "-id").values("source_revision")[:1])
    return CodeVersion.objects.annotate(proven_revision=Subquery(newest))


@never_cache
def versions_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "code.version.read")
    if bad:
        return bad
    result, bad = _page(request, _versions(), "code-version-list", _version)
    return bad or response(request, result)


@never_cache
def version_detail_view(request, version_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "code.version.read")
    if bad:
        return bad
    if request.GET:
        return error(request, 400, "VALIDATION_FAILED", "不支持该查询参数。")
    row = _versions().filter(pk=version_id).first()
    if row is None:
        return error(request, 404, "NOT_FOUND", "代码版本不存在。")
    return response(request, _version(row, verify_digest=True))


@never_cache
def jobs_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "code.version.read")
    if bad:
        return bad
    result, bad = _page(request, CodeBuildJob.objects.all(), "code-job-list", _job)
    return bad or response(request, result)
