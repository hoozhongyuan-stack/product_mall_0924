"""Admin configuration/authorization and authenticated WeChat event entry points."""
from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.vary import vary_on_cookie

from accounts.models import AdminAccount
from accounts.read_rate_limit import read_rate_limit
from accounts.security import audit, confirm_action, require, require_live
from common.http import error, method, parse_json_object, response
from wechat_integration.credentials import CredentialsUnavailable

from .client import PlatformRejected, PlatformUnavailable
from .crypto import InvalidCallback
from .service import (PlatformStateError, authorization_status, begin_authorization,
                      component_settings, configure_component, finish_authorization,
                      receive_encrypted_event)

OBJECT_ID = 'wechat-open-platform'


def _admin(request, *, manage=False, live=False):
    checker = require_live if live else require
    account, bad = checker(request, 'wechat.integration.read')
    if not bad and manage:
        account, bad = checker(request, 'wechat.integration.manage')
    return account, bad


def _service_error(request, exc):
    if isinstance(exc, PlatformStateError):
        return error(request, 409, 'PLATFORM_STATE', str(exc))
    if isinstance(exc, CredentialsUnavailable):
        return error(request, 503, 'CREDENTIALS_UNAVAILABLE', str(exc))
    if isinstance(exc, PlatformRejected):
        return error(request, 409, 'WECHAT_REJECTED', '微信接口拒绝该操作。', [{'errcode': exc.code}])
    return error(request, 503, 'PLATFORM_UNAVAILABLE', '微信接口暂不可用，操作结果需要人工核对。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def component_config_view(request):
    bad = method(request, 'GET', 'PUT')
    if bad:
        return bad
    actor, bad = _admin(request, manage=request.method == 'PUT')
    if bad:
        return bad
    if request.method == 'GET':
        bad = read_rate_limit(request, actor, 'wechat.integration')
        return bad or response(request, component_settings())
    try:
        body = parse_json_object(request, max_bytes=8192)
        expected = {'componentAppId', 'developerAppId', 'redirectUri', 'componentAppSecret',
                    'messageToken', 'encodingAesKey', 'expectedRevision'}
        if set(body) != expected:
            raise ValueError()
        with transaction.atomic():
            AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
            actor, bad = _admin(request, manage=True, live=True)
            if bad:
                return bad
            current = component_settings()
            if body['expectedRevision'] != current['revision']:
                return error(request, 409, 'REVISION_CONFLICT', '配置已变化，请重新读取后确认。')
            bad = confirm_action(request, actor, 'wechat.integration.update', OBJECT_ID,
                                 current['revision'])
            if bad:
                return bad
            result = configure_component(
                component_app_id=body['componentAppId'], developer_app_id=body['developerAppId'],
                redirect_uri=body['redirectUri'], component_app_secret=body['componentAppSecret'],
                message_token=body['messageToken'], encoding_aes_key=body['encodingAesKey'],
                expected_revision=current['revision'])
            audit(request, 'wechat.open_platform.configure', 'wechat_open_platform', OBJECT_ID,
                  actor, before={'revision': current['revision']},
                  after={'revision': result['revision']})
        return response(request, result)
    except (PlatformStateError, CredentialsUnavailable) as exc:
        return _service_error(request, exc)
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '第三方平台配置请求格式错误。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def begin_authorization_view(request):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = _admin(request, manage=True)
    if bad:
        return bad
    try:
        body = parse_json_object(request, max_bytes=2048)
        if set(body) != {'targetAppId'}:
            raise ValueError()
        started = begin_authorization(body['targetAppId'], actor=actor)
        audit(request, 'wechat.open_platform.authorize.start', 'wechat_authorizer',
              body['targetAppId'], actor)
        return response(request, {'url': started['url'], 'targetAppId': started['targetAppId']})
    except (PlatformStateError, CredentialsUnavailable, PlatformUnavailable, PlatformRejected) as exc:
        return _service_error(request, exc)
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '目标小程序 AppID 格式错误。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def authorization_status_view(request, target_app_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    actor, bad = _admin(request)
    if bad:
        return bad
    bad = read_rate_limit(request, actor, 'wechat.integration')
    return bad or response(request, authorization_status(target_app_id))


@cache_control(private=True, no_store=True)
def authorization_callback_view(request):
    bad = method(request, 'GET')
    if bad:
        return bad
    if set(request.GET) != {'state', 'auth_code', 'expires_in'}:
        return error(request, 400, 'VALIDATION_FAILED', '授权回调参数不完整。')
    try:
        result = finish_authorization(request.GET['state'], request.GET['auth_code'])
        # The caller sees only a completion marker; no token or code is returned.
        return response(request, result)
    except (PlatformStateError, CredentialsUnavailable, PlatformUnavailable, PlatformRejected) as exc:
        return _service_error(request, exc)


@csrf_exempt
@cache_control(private=True, no_store=True)
def encrypted_event_view(request):
    bad = method(request, 'POST')
    if bad:
        return bad
    if len(request.body) > 131072:
        return HttpResponse('invalid', status=400, content_type='text/plain')
    try:
        receive_encrypted_event(request.body,
                                signature=request.GET.get('msg_signature', ''),
                                timestamp=request.GET.get('timestamp', ''),
                                nonce=request.GET.get('nonce', ''))
    except (InvalidCallback, PlatformStateError, CredentialsUnavailable):
        return HttpResponse('invalid', status=403, content_type='text/plain')
    except (PlatformUnavailable, PlatformRejected):
        return HttpResponse('retry', status=503, content_type='text/plain')
    return HttpResponse('success', content_type='text/plain')
