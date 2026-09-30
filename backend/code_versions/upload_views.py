"""Queue direct CI developer-version uploads; never claim review or release."""
import hashlib
import json
import re
import uuid

from django.db import IntegrityError, transaction
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_cookie

from accounts.models import AdminAccount
from accounts.security import audit, confirm_action, require, require_live
from common.http import error, method, parse_json_object, response
from wechat_integration.credentials import CredentialsUnavailable, effective_credentials, integration_row
from wechat_open_platform.service import PlatformStateError, authorization_status, developer_app_id

from .models import CodeUploadKey, CodeVersion, DeveloperUploadKey, ReleaseReviewJob, ReleaseUploadJob
from .release_service import (APP_ID, UploadKeyUnavailable, _package_config,
                              decrypt_developer_upload_key, decrypt_upload_key)
from .upload_diagnostics import public_diagnostic
from .views import _page


UPLOAD_VERSION = re.compile(r'[0-9A-Za-z][0-9A-Za-z._-]{0,39}\Z')


def _dto(job):
    failure_message, next_action = public_diagnostic(job)
    return {'taskId': str(job.pk), 'versionId': str(job.version_id), 'appId': job.app_id,
            'version': job.upload_version, 'description': job.description,
            'channel': job.channel, 'developerAppId': job.developer_app_id,
            'packageSha256': job.package_sha256, 'stagedDigest': job.staged_digest or None,
            'reviewAvailable': job.channel == 'DIRECT_COMMIT' and job.status == 'SUCCEEDED',
            'status': job.status, 'failureCode': job.failure_code,
            'failureStage': job.failure_stage or None, 'sdkCode': job.sdk_code or None,
            'platformErrorCode': job.platform_error_code,
            'innerPlatformErrorCode': job.inner_platform_error_code,
            'platformReason': job.platform_reason or None,
            'failureMessage': failure_message, 'nextAction': next_action,
            'resolutionNote': job.resolution_note,
            'createdAt': job.created_at.isoformat(),
            'completedAt': job.completed_at.isoformat() if job.completed_at else None}


def _read(request):
    return require(request, 'code.version.read')


def _write(request, *, live=False):
    checker = require_live if live else require
    actor, bad = checker(request, 'code.version.read')
    if bad:
        return actor, bad
    return checker(request, 'code.release.manage')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def uploads_view(request):
    bad = method(request, 'GET', 'POST')
    if bad:
        return bad
    if request.method == 'GET':
        _, bad = _read(request)
        if bad:
            return bad
        data, bad = _page(request, ReleaseUploadJob.objects.all(), 'code-release-uploads', _dto)
        return bad or response(request, data, meta={'nextCursor': data['nextCursor']})
    return _create(request)


@cache_control(private=True, no_store=True)
@vary_on_cookie
def upload_detail_view(request, task_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, bad = _read(request)
    if bad:
        return bad
    if request.GET:
        return error(request, 400, 'VALIDATION_FAILED', '不支持该查询参数。')
    job = ReleaseUploadJob.objects.filter(pk=task_id).first()
    return response(request, _dto(job)) if job else error(request, 404, 'NOT_FOUND', '上传任务不存在。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def resolve_upload_view(request, task_id):
    """After external inspection, close UNKNOWN without asserting upload success."""
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = _write(request)
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
        actor, bad = _write(request, live=True)
        if bad:
            return bad
        job = ReleaseUploadJob.objects.select_for_update().filter(pk=task_id).first()
        if not job:
            return error(request, 404, 'NOT_FOUND', '上传任务不存在。')
        if job.status != 'UNKNOWN':
            return error(request, 409, 'NOT_UNKNOWN', '仅结果未知的上传任务可关闭。')
        bad = confirm_action(request, actor, 'code.release.resolve_upload', str(job.pk), 0)
        if bad:
            return bad
        job.status = 'RESOLVED'
        job.resolution_note = note
        job.save(update_fields=['status', 'resolution_note'])
        audit(request, 'code.release.upload.resolve', 'code_upload_job', job.pk, actor,
              after={'status': 'RESOLVED', 'note': note})
    return response(request, _dto(job))


def _create(request):
    actor, bad = _write(request)
    if bad:
        return bad
    try:
        body = parse_json_object(request, max_bytes=2048)
        if set(body) not in ({'versionId', 'version', 'description'},
                             {'versionId', 'version', 'description', 'channel'}):
            raise ValueError()
        version_id = uuid.UUID(body['versionId'])
        label, description = body['version'], body['description']
        channel = body.get('channel', 'CI_DIRECT')
        if (not isinstance(label, str) or not UPLOAD_VERSION.fullmatch(label) or
                not isinstance(description, str) or len(description) > 100 or
                channel not in {'CI_DIRECT', 'DIRECT_COMMIT'} or
                any(ord(c) < 32 or ord(c) == 127 for c in label + description)):
            raise ValueError()
        key = uuid.UUID(request.headers.get('Idempotency-Key', ''))
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                                           ensure_ascii=False).encode()).hexdigest()
    except (ValueError, TypeError, KeyError, AttributeError):
        return error(request, 400, 'VALIDATION_FAILED', '请提供版本、说明与 UUID 幂等键。')
    prior = ReleaseUploadJob.objects.filter(request_key=key).first()
    if prior:
        if prior.request_hash != digest or prior.created_by_id != actor.pk:
            return error(request, 409, 'IDEMPOTENCY_CONFLICT', '该幂等键已用于不同请求。')
        return response(request, _dto(prior))
    if channel == 'DIRECT_COMMIT':
        try:
            target_app_id = effective_credentials(integration_row()).app_id
            authorization = authorization_status(target_app_id)
            configured_developer = developer_app_id()
        except (PlatformStateError, CredentialsUnavailable):
            return error(request, 503, 'PLATFORM_UNAVAILABLE', '第三方平台授权状态暂不可验证。')
        if authorization.get('status') != 'PASS':
            return error(request, 409, 'AUTHORIZATION_NOT_READY',
                         '目标小程序授权或代码管理权限尚未验证。')
        if not APP_ID.fullmatch(configured_developer or '') or configured_developer == target_app_id:
            return error(request, 409, 'DEVELOPER_APP_ID_UNAVAILABLE', '请配置不同的开发小程序 AppID。')
    try:
        with transaction.atomic():
            key_model = DeveloperUploadKey if channel == 'DIRECT_COMMIT' else CodeUploadKey
            key_row = key_model.objects.select_for_update().get(pk=1)
            AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
            actor, bad = _write(request, live=True)
            if bad:
                return bad
            existing = ReleaseUploadJob.objects.filter(request_key=key).first()
            if existing:
                if existing.request_hash != digest or existing.created_by_id != actor.pk:
                    return error(request, 409, 'IDEMPOTENCY_CONFLICT', '该幂等键已用于不同请求。')
                return response(request, _dto(existing))
            version = CodeVersion.objects.filter(pk=version_id).first()
            if not version:
                return error(request, 404, 'NOT_FOUND', '代码版本不存在。')
            snapshot = effective_credentials(integration_row())
            app_id = snapshot.app_id
            if not APP_ID.fullmatch(app_id or ''):
                return error(request, 409, 'APP_ID_UNAVAILABLE', '请先配置有效的小程序 AppID。')
            checks = _package_config(version, app_id)
            if any(item['status'] != 'PASS' for item in checks):
                return error(request, 409, 'PACKAGE_NOT_READY', '所选代码包未通过发布配置检查。')
            developer_id = configured_developer if channel == 'DIRECT_COMMIT' else app_id
            if key_row.app_id != developer_id or not key_row.encrypted_payload:
                return error(request, 409, 'UPLOAD_KEY_UNAVAILABLE', '请先保存对应小程序的代码上传私钥。')
            try:
                (decrypt_developer_upload_key if channel == 'DIRECT_COMMIT' else decrypt_upload_key)(key_row)
            except UploadKeyUnavailable:
                return error(request, 409, 'UPLOAD_KEY_UNAVAILABLE', '代码上传私钥不可用，请重新上传。')
            if channel == 'DIRECT_COMMIT' and developer_app_id() != developer_id:
                return error(request, 409, 'DEVELOPER_APP_ID_CHANGED', '开发小程序配置已变化，请刷新。')
            if ReleaseUploadJob.objects.filter(app_id=app_id,
                    status__in=['PENDING', 'RUNNING', 'UNKNOWN']).exists():
                return error(request, 409, 'UPLOAD_IN_PROGRESS', '当前小程序仍有未确认的上传任务。')
            if ReleaseReviewJob.objects.filter(app_id=app_id, status__in=[
                    'SUBMITTING', 'SUBMITTED', 'REVIEWING', 'APPROVED', 'UNKNOWN',
                    'RELEASING', 'RELEASE_UNKNOWN', 'RELEASE_REQUESTED']).exists():
                return error(request, 409, 'REVIEW_IN_PROGRESS', '当前小程序有未结束的审核或发布任务，不能覆盖待审核代码。')
            confirmation_target = f'{app_id}:{version.pk}' if channel == 'CI_DIRECT' else f'{app_id}:{version.pk}:DIRECT_COMMIT'
            bad = confirm_action(request, actor, 'code.release.upload',
                                 confirmation_target, key_row.revision)
            if bad:
                return bad
            job = ReleaseUploadJob.objects.create(version=version, app_id=app_id,
                developer_app_id=developer_id, channel=channel,
                package_sha256=version.package_sha256, key_revision=key_row.revision,
                upload_version=label, description=description, request_key=key,
                request_hash=digest, created_by=actor)
            audit(request, 'code.release.upload.request', 'code_upload_job', job.pk, actor,
                  after={'versionId': str(version.pk), 'status': 'PENDING'})
            return response(request, _dto(job), status=202)
    except (CodeUploadKey.DoesNotExist, DeveloperUploadKey.DoesNotExist, CredentialsUnavailable):
        return error(request, 503, 'CONFIGURATION_UNAVAILABLE', '小程序配置或部署密钥暂不可用。')
    except IntegrityError:
        return error(request, 409, 'UPLOAD_IN_PROGRESS', '上传请求发生并发冲突，请刷新任务列表。')
