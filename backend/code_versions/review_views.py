"""Explicit audit and release actions for a successful directCommit upload."""
import hashlib
import json
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_cookie

from accounts.models import AdminAccount
from accounts.read_rate_limit import read_rate_limit
from accounts.security import audit, confirm_action, require, require_live
from common.http import error, method, parse_json_object, response
from wechat_open_platform.client import PlatformRejected, PlatformUnavailable
from wechat_open_platform.service import (PlatformStateError, authorization_status,
    get_audit_status, get_category, release_approved, submit_audit)

from .models import DeveloperUploadKey, ReleaseReviewJob, ReleaseUploadJob
from .views import _page


ACTIVE = ['SUBMITTING', 'SUBMITTED', 'REVIEWING', 'APPROVED', 'UNKNOWN',
          'RELEASING', 'RELEASE_UNKNOWN', 'RELEASE_REQUESTED']
CATEGORY_FIELDS = ('first_class', 'second_class', 'first_id', 'second_id')


def _dto(job):
    return {'taskId': str(job.pk), 'uploadTaskId': str(job.upload_id), 'appId': job.app_id,
            'version': job.user_version, 'auditId': job.audit_id, 'status': job.status,
            'failureCode': job.failure_code, 'reason': job.reason,
            'resolutionNote': job.resolution_note,
            'createdAt': job.created_at.isoformat(), 'updatedAt': job.updated_at.isoformat(),
            'releaseRequestedAt': job.release_requested_at.isoformat() if job.release_requested_at else None}


def _access(request, *, write=False, live=False):
    checker = require_live if live else require
    actor, bad = checker(request, 'code.version.read')
    if not bad and write:
        actor, bad = checker(request, 'code.release.manage')
    return actor, bad


def _available_categories(target):
    if authorization_status(target).get('status') != 'PASS':
        raise PlatformStateError('目标小程序授权及代码管理权限未通过微信核验。')
    raw = get_category(target)
    return [{field: item[field] for field in CATEGORY_FIELDS}
            for item in raw if all(type(item.get(field)) in (str, int) and
                                    str(item[field]).strip() for field in CATEGORY_FIELDS)]


@cache_control(private=True, no_store=True)
@vary_on_cookie
def categories_view(request):
    bad = method(request, 'GET')
    if bad:
        return bad
    actor, bad = _access(request)
    if bad:
        return bad
    if request.GET:
        return error(request, 400, 'VALIDATION_FAILED', '不支持该查询参数。')
    bad = read_rate_limit(request, actor, 'code.release')
    if bad:
        return bad
    target = ReleaseUploadJob.objects.filter(channel='DIRECT_COMMIT', status='SUCCEEDED').order_by('-completed_at').first()
    if not target:
        return error(request, 409, 'UPLOAD_REQUIRED', '请先成功上传待审核版本。')
    try:
        return response(request, {'items': _available_categories(target.app_id)})
    except (PlatformStateError, PlatformRejected, PlatformUnavailable):
        return error(request, 503, 'CATEGORY_UNAVAILABLE', '微信审核类目暂不可验证，请稍后重试。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def reviews_view(request):
    bad = method(request, 'GET', 'POST')
    if bad:
        return bad
    if request.method == 'GET':
        _, bad = _access(request)
        if bad:
            return bad
        data, bad = _page(request, ReleaseReviewJob.objects.all(), 'code-release-reviews', _dto)
        return bad or response(request, data, meta={'nextCursor': data['nextCursor']})
    return _submit(request)


def _validated_submit(request):
    body = parse_json_object(request, max_bytes=4096)
    if set(body) != {'uploadTaskId', 'itemList', 'versionDesc'}:
        raise ValueError()
    upload_id = uuid.UUID(body['uploadTaskId'])
    items, desc = body['itemList'], body['versionDesc']
    if (not isinstance(items, list) or not 1 <= len(items) <= 5
            or not isinstance(desc, str) or not 1 <= len(desc) <= 200
            or any(ord(char) < 32 for char in desc)):
        raise ValueError()
    normalized = []
    for item in items:
        if (not isinstance(item, dict) or set(item) != set(CATEGORY_FIELDS)
                or any(type(item[field]) not in (str, int) or
                       not str(item[field]).strip() or len(str(item[field])) > 100
                       for field in CATEGORY_FIELDS)):
            raise ValueError()
        normalized.append({field: item[field] for field in CATEGORY_FIELDS})
    request_key = uuid.UUID(request.headers.get('Idempotency-Key', ''))
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return upload_id, normalized, desc, request_key, digest


def _submit(request):
    actor, bad = _access(request, write=True)
    if bad:
        return bad
    try:
        upload_id, items, desc, request_key, digest = _validated_submit(request)
    except (ValueError, TypeError, KeyError, AttributeError):
        return error(request, 400, 'VALIDATION_FAILED', '请提供上传任务、审核类目、版本说明与 UUID 幂等键。')
    existing = ReleaseReviewJob.objects.filter(request_key=request_key).first()
    if existing:
        if existing.request_hash != digest or existing.created_by_id != actor.pk:
            return error(request, 409, 'IDEMPOTENCY_CONFLICT', '该幂等键已用于其他请求。')
        return response(request, _dto(existing))
    upload = ReleaseUploadJob.objects.filter(pk=upload_id).first()
    if not upload or upload.channel != 'DIRECT_COMMIT' or upload.status != 'SUCCEEDED':
        return error(request, 409, 'UPLOAD_REQUIRED', '请选择成功上传至待审核列表的版本。')
    try:
        categories = _available_categories(upload.app_id)
    except (PlatformStateError, PlatformRejected, PlatformUnavailable):
        return error(request, 503, 'CATEGORY_UNAVAILABLE', '授权或微信审核类目暂不可验证。')
    if not all(item in categories for item in items):
        return error(request, 409, 'CATEGORY_CHANGED', '审核类目已变化，请重新读取并选择。')
    try:
        with transaction.atomic():
            AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
            actor, bad = _access(request, live=True, write=True)
            if bad:
                return bad
            DeveloperUploadKey.objects.select_for_update().get(pk=1)
            upload = ReleaseUploadJob.objects.select_for_update().get(pk=upload_id)
            if upload.status != 'SUCCEEDED':
                return error(request, 409, 'UPLOAD_CHANGED', '上传状态已变化，请重新读取。')
            latest = ReleaseUploadJob.objects.filter(app_id=upload.app_id).order_by('-created_at', '-id').first()
            if not latest or latest.pk != upload.pk:
                return error(request, 409, 'NEWER_UPLOAD_EXISTS', '已有更新的上传任务，请核对微信待审核版本。')
            if ReleaseReviewJob.objects.filter(request_key=request_key).exists():
                return error(request, 409, 'IDEMPOTENCY_CONFLICT', '提审请求已存在，请重新读取。')
            if ReleaseReviewJob.objects.filter(app_id=upload.app_id, status__in=ACTIVE).exists():
                return error(request, 409, 'REVIEW_IN_PROGRESS', '当前小程序已有未结束的审核或发布任务。')
            bad = confirm_action(request, actor, 'code.release.review', str(upload_id), 0)
            if bad:
                return bad
            job = ReleaseReviewJob.objects.create(upload=upload, app_id=upload.app_id,
                user_version=upload.upload_version, item_list=items, version_desc=desc,
                request_key=request_key, request_hash=digest, created_by=actor)
            audit(request, 'code.release.review.request', 'code_review_job', job.pk, actor,
                  after={'uploadTaskId': str(upload_id), 'status': 'SUBMITTING'})
    except (DeveloperUploadKey.DoesNotExist, ReleaseUploadJob.DoesNotExist, IntegrityError):
        return error(request, 409, 'REVIEW_IN_PROGRESS', '提审任务状态已变化，请刷新。')
    try:
        audit_id = submit_audit(job.app_id, item_list=items, version_desc=desc)
        status, code = 'SUBMITTED', ''
    except PlatformRejected:
        audit_id, status, code = None, 'FAILED', 'WECHAT_REJECTED'
    except (PlatformUnavailable, PlatformStateError):
        audit_id, status, code = None, 'UNKNOWN', 'RESULT_UNKNOWN'
    with transaction.atomic():
        current = ReleaseReviewJob.objects.select_for_update().get(pk=job.pk)
        if current.status == 'SUBMITTING':
            current.audit_id, current.status, current.failure_code = audit_id, status, code
            current.save(update_fields=['audit_id', 'status', 'failure_code', 'updated_at'])
    return response(request, _dto(current), status=202)


@cache_control(private=True, no_store=True)
@vary_on_cookie
def review_detail_view(request, task_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, bad = _access(request)
    if bad:
        return bad
    job = ReleaseReviewJob.objects.filter(pk=task_id).first()
    return response(request, _dto(job)) if job else error(request, 404, 'NOT_FOUND', '提审任务不存在。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def refresh_review_view(request, task_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = _access(request, write=True)
    if bad:
        return bad
    job = ReleaseReviewJob.objects.filter(pk=task_id).first()
    if not job:
        return error(request, 404, 'NOT_FOUND', '提审任务不存在。')
    if not job.audit_id or job.status not in {'SUBMITTED', 'REVIEWING', 'APPROVED', 'REJECTED'}:
        return error(request, 409, 'AUDIT_ID_UNAVAILABLE', '当前没有可查询的微信审核单号。')
    try:
        result = get_audit_status(job.app_id, job.audit_id)
    except (PlatformUnavailable, PlatformRejected, PlatformStateError):
        return error(request, 503, 'AUDIT_STATUS_UNKNOWN', '微信审核状态暂不可验证，原记录未变更。')
    state = {0: 'APPROVED', 1: 'REJECTED'}.get(result['status'], 'REVIEWING')
    with transaction.atomic():
        current = ReleaseReviewJob.objects.select_for_update().get(pk=task_id)
        if current.status in {'SUBMITTED', 'REVIEWING', 'APPROVED', 'REJECTED'}:
            current.status = state
            current.reason = str(result.get('reason') or '')[:500]
            current.save(update_fields=['status', 'reason', 'updated_at'])
    audit(request, 'code.release.review.refresh', 'code_review_job', job.pk, actor,
          after={'status': current.status})
    return response(request, _dto(current))


@cache_control(private=True, no_store=True)
@vary_on_cookie
def release_review_view(request, task_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = _access(request, write=True)
    if bad:
        return bad
    try:
        body = parse_json_object(request, max_bytes=256)
        if body != {}:
            raise ValueError()
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '发布请求格式错误。')
    with transaction.atomic():
        AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
        actor, bad = _access(request, live=True, write=True)
        if bad:
            return bad
        job = ReleaseReviewJob.objects.select_for_update().filter(pk=task_id).first()
        if not job:
            return error(request, 404, 'NOT_FOUND', '提审任务不存在。')
        if job.status != 'APPROVED' or not job.audit_id:
            return error(request, 409, 'NOT_APPROVED', '该审核单未处于审核通过状态。')
        bad = confirm_action(request, actor, 'code.release.publish', str(job.pk), job.audit_id)
        if bad:
            return bad
        job.status = 'RELEASING'
        job.save(update_fields=['status', 'updated_at'])
        audit(request, 'code.release.publish.request', 'code_review_job', job.pk, actor,
              after={'auditId': job.audit_id, 'status': 'RELEASING'})
    try:
        release_approved(job.app_id, auditid=job.audit_id, user_version=job.user_version)
        status, code = 'RELEASE_REQUESTED', ''
    except (PlatformUnavailable, PlatformRejected, PlatformStateError):
        status, code = 'RELEASE_UNKNOWN', 'RESULT_UNKNOWN'
    with transaction.atomic():
        current = ReleaseReviewJob.objects.select_for_update().get(pk=job.pk)
        if current.status == 'RELEASING':
            current.status, current.failure_code = status, code
            if status == 'RELEASE_REQUESTED':
                current.release_requested_at = timezone.now()
            current.save(update_fields=['status', 'failure_code', 'release_requested_at', 'updated_at'])
    return response(request, _dto(current), status=202)


@cache_control(private=True, no_store=True)
@vary_on_cookie
def resolve_review_view(request, task_id):
    """Manual closure preserves an unknown outcome and never permits publishing it."""
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = _access(request, write=True)
    if bad:
        return bad
    try:
        body = parse_json_object(request, max_bytes=1024)
        if set(body) != {'note'} or not isinstance(body['note'], str):
            raise ValueError()
        note = body['note'].strip()
        if not 20 <= len(note) <= 500 or any(ord(char) < 32 for char in note):
            raise ValueError()
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '请填写 20—500 字的平台核查说明。')
    with transaction.atomic():
        AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
        actor, bad = _access(request, live=True, write=True)
        if bad:
            return bad
        job = ReleaseReviewJob.objects.select_for_update().filter(pk=task_id).first()
        if not job:
            return error(request, 404, 'NOT_FOUND', '提审任务不存在。')
        if job.status not in {'SUBMITTING', 'UNKNOWN', 'RELEASING', 'RELEASE_UNKNOWN',
                              'RELEASE_REQUESTED', 'APPROVED'}:
            return error(request, 409, 'NOT_CLOSABLE', '当前审核或发布任务不可关闭。')
        if job.status in {'SUBMITTING', 'RELEASING', 'RELEASE_REQUESTED'} and timezone.now() - job.updated_at < timedelta(minutes=5):
            return error(request, 409, 'ATTEMPT_IN_PROGRESS', '微信调用仍可能进行中，请稍后核查。')
        bad = confirm_action(request, actor, 'code.release.resolve_review', str(job.pk), 0)
        if bad:
            return bad
        job.status = 'CLOSED_UNVERIFIED'
        job.resolution_note = note
        job.save(update_fields=['status', 'resolution_note', 'updated_at'])
        audit(request, 'code.release.review.resolve', 'code_review_job', job.pk, actor,
              after={'status': job.status, 'note': note})
    return response(request, _dto(job))
