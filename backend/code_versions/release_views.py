"""Private release preflight; no WeChat release side effects."""
from django.db import transaction
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_cookie

from accounts.models import AdminAccount
from accounts.read_rate_limit import read_rate_limit
from accounts.security import audit, confirm_action, require, require_live
from common.http import error, method, parse_json_object, response
from wechat_integration.credentials import CredentialsUnavailable, effective_credentials, integration_row
from wechat_open_platform.service import PlatformStateError, developer_app_id

from .models import CodeUploadKey, DeveloperUploadKey
from .release_service import (APP_ID, encrypt_upload_key, encrypt_developer_upload_key,
                              readiness_data, upload_key_data,
                              validate_upload_key)


def _access(request, *, live=False, write=False):
    checker = require_live if live else require
    actor, bad = checker(request, 'code.version.read')
    if not bad and write:
        actor, bad = checker(request, 'code.release.manage')
    return actor, bad


@cache_control(private=True, no_store=True)
@vary_on_cookie
def readiness_view(request):
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
    return response(request, readiness_data())


@cache_control(private=True, no_store=True)
@vary_on_cookie
def upload_key_view(request):
    bad = method(request, 'GET', 'PUT')
    if bad:
        return bad
    actor, bad = _access(request, write=True)
    if bad:
        return bad
    if request.method == 'GET':
        if request.GET:
            return error(request, 400, 'VALIDATION_FAILED', '不支持该查询参数。')
        return response(request, upload_key_data(CodeUploadKey.objects.filter(pk=1).first() or CodeUploadKey()))
    try:
        body = parse_json_object(request, max_bytes=24576)
        if set(body) != {'appId', 'key', 'expectedRevision'}:
            raise ValueError()
        app_id, expected = body['appId'], body['expectedRevision']
        if (not isinstance(app_id, str) or not APP_ID.fullmatch(app_id)
                or type(expected) is not int or expected < 0):
            raise ValueError()
        private_key = validate_upload_key(body['key'])
        with transaction.atomic():
            row = CodeUploadKey.objects.select_for_update().get(pk=1)
            if row.revision != expected:
                return error(request, 409, 'REVISION_CONFLICT', '配置已变化，请重新读取后确认。',
                             [{'currentRevision': row.revision}])
            snapshot = effective_credentials(integration_row())
            if app_id != snapshot.app_id:
                return error(request, 409, 'APP_ID_MISMATCH', '密钥 AppID 与当前小程序配置不一致。')
            AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
            actor, bad = _access(request, live=True, write=True)
            if bad:
                return bad
            bad = confirm_action(request, actor, 'code.release.upload_key', app_id, expected)
            if bad:
                return bad
            previous_revision = row.revision
            row.encrypted_payload = encrypt_upload_key(app_id, private_key)
            row.app_id = app_id
            row.revision += 1
            row.updated_by = actor
            row.save()
            audit(request, 'code.release.upload_key', 'code_upload_key', app_id, actor,
                  before={'revision': previous_revision}, after={'revision': row.revision})
            return response(request, upload_key_data(row))
    except CodeUploadKey.DoesNotExist:
        return error(request, 503, 'CONFIGURATION_UNAVAILABLE', '代码上传密钥配置不存在，请检查数据库迁移。')
    except CredentialsUnavailable:
        return error(request, 503, 'CREDENTIALS_UNAVAILABLE', '小程序凭据或部署密钥暂不可用。')
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '请上传有效的 PEM RSA 代码上传私钥和配置修订号。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def developer_key_view(request):
    """The third-party development Mini Program key has a distinct purpose."""
    bad = method(request, 'GET', 'PUT')
    if bad:
        return bad
    actor, bad = _access(request, write=True)
    if bad:
        return bad
    if request.method == 'GET':
        if request.GET:
            return error(request, 400, 'VALIDATION_FAILED', '不支持该查询参数。')
        row = DeveloperUploadKey.objects.filter(pk=1).first() or DeveloperUploadKey()
        return response(request, upload_key_data(row))
    try:
        body = parse_json_object(request, max_bytes=24576)
        if set(body) != {'appId', 'key', 'expectedRevision'}:
            raise ValueError()
        app_id, expected = body['appId'], body['expectedRevision']
        if (not isinstance(app_id, str) or not APP_ID.fullmatch(app_id)
                or type(expected) is not int or expected < 0):
            raise ValueError()
        private_key = validate_upload_key(body['key'])
        configured = developer_app_id()
        if app_id != configured:
            return error(request, 409, 'APP_ID_MISMATCH', '密钥 AppID 与第三方平台开发小程序不一致。')
        with transaction.atomic():
            row = DeveloperUploadKey.objects.select_for_update().get(pk=1)
            if row.revision != expected:
                return error(request, 409, 'REVISION_CONFLICT', '配置已变化，请重新读取后确认。',
                             [{'currentRevision': row.revision}])
            AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
            actor, bad = _access(request, live=True, write=True)
            if bad:
                return bad
            if app_id != developer_app_id():
                return error(request, 409, 'APP_ID_MISMATCH', '开发小程序 AppID 已变化，请重新读取。')
            bad = confirm_action(request, actor, 'code.release.developer_upload_key', app_id, expected)
            if bad:
                return bad
            previous_revision = row.revision
            row.encrypted_payload = encrypt_developer_upload_key(app_id, private_key)
            row.app_id = app_id
            row.revision += 1
            row.updated_by = actor
            row.save()
            audit(request, 'code.release.developer_upload_key', 'developer_upload_key', app_id, actor,
                  before={'revision': previous_revision}, after={'revision': row.revision})
            return response(request, upload_key_data(row))
    except DeveloperUploadKey.DoesNotExist:
        return error(request, 503, 'CONFIGURATION_UNAVAILABLE', '开发小程序密钥配置不存在，请检查数据库迁移。')
    except PlatformStateError:
        return error(request, 503, 'PLATFORM_UNAVAILABLE', '第三方平台功能尚未部署。')
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '请上传有效的开发小程序 PEM RSA 私钥。')
