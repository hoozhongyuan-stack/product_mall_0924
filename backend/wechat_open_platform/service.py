"""Third-party authorization and code-review API; no implicit retry or publication."""
import base64
import binascii
import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.db import transaction
from django.utils import timezone

from wechat_integration.credentials import CredentialsUnavailable

from .client import PlatformRejected, PlatformUnavailable, call
from .crypto import decode_callback, seal, unseal
from .models import AuthorizationIntent, AuthorizerGrant, ComponentConfig

APP_ID = re.compile(r'wx[a-zA-Z0-9]{16}\Z')
TOKEN_MARGIN = timedelta(minutes=5)


class PlatformStateError(ValueError):
    pass


def _config():
    row = ComponentConfig.objects.filter(pk=1).first()
    if row is None:
        raise PlatformStateError('第三方平台配置尚未初始化。')
    return row


def developer_app_id():
    return _config().developer_app_id


def component_settings():
    row = _config()
    return {'componentAppId': row.component_app_id, 'developerAppId': row.developer_app_id,
            'redirectUri': row.redirect_uri, 'configured': bool(row.encrypted_credentials),
            'ticketReceived': bool(row.encrypted_ticket), 'revision': row.revision}


def _redirect_uri(value):
    if not isinstance(value, str) or len(value) > 512:
        raise PlatformStateError('授权回调地址格式错误。')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.path != '/api/v1/wechat/open-platform/authorization-callback'
            or parsed.username or parsed.password or parsed.fragment or parsed.query):
        raise PlatformStateError('授权回调地址必须是固定的 HTTPS 地址，且不能包含查询或片段。')
    return value


def configure_component(*, component_app_id, developer_app_id, redirect_uri,
                        component_app_secret, message_token, encoding_aes_key,
                        expected_revision):
    if (not isinstance(component_app_id, str) or not APP_ID.fullmatch(component_app_id)
            or not isinstance(developer_app_id, str) or not APP_ID.fullmatch(developer_app_id)
            or component_app_id == developer_app_id
            or not isinstance(component_app_secret, str) or not (1 <= len(component_app_secret) <= 256)
            or not isinstance(message_token, str) or not (1 <= len(message_token) <= 128)
            or not isinstance(encoding_aes_key, str) or len(encoding_aes_key) != 43
            or type(expected_revision) is not int or expected_revision < 0):
        raise PlatformStateError('第三方平台配置格式错误。')
    try:
        if len(base64.b64decode(encoding_aes_key + '=', validate=True)) != 32:
            raise ValueError()
    except (ValueError, binascii.Error):
        raise PlatformStateError('消息加解密 Key 格式错误。') from None
    redirect_uri = _redirect_uri(redirect_uri)
    encrypted = seal('open-platform-credentials', {
        'componentAppSecret': component_app_secret, 'messageToken': message_token,
        'encodingAesKey': encoding_aes_key,
    })
    with transaction.atomic():
        row = ComponentConfig.objects.select_for_update().get(pk=1)
        if row.revision != expected_revision:
            raise PlatformStateError('配置已变化，请重新读取后确认。')
        identity_changed = row.component_app_id != component_app_id
        row.component_app_id = component_app_id
        row.developer_app_id = developer_app_id
        row.redirect_uri = redirect_uri
        row.encrypted_credentials = encrypted
        row.encrypted_access_token = ''
        row.access_token_expires_at = None
        if identity_changed:
            row.encrypted_ticket = ''
            row.ticket_received_at = None
            row.ticket_event_time = 0
        row.revision += 1
        row.save()
    return component_settings()


def _credentials(row):
    if not row.component_app_id or not row.encrypted_credentials:
        raise PlatformStateError('请先配置第三方平台凭据。')
    value = unseal('open-platform-credentials', row.encrypted_credentials)
    if (not isinstance(value, dict) or not all(isinstance(value.get(x), str) and value[x]
            for x in ('componentAppSecret', 'messageToken', 'encodingAesKey'))):
        raise CredentialsUnavailable()
    return value


def _safe_token(purpose, encrypted, expires_at):
    if not encrypted or not expires_at or expires_at <= timezone.now() + TOKEN_MARGIN:
        return None
    value = unseal(purpose, encrypted)
    if not isinstance(value, str) or not value:
        raise CredentialsUnavailable()
    return value


def _token_result(result, key):
    token, expires = result.get(key), result.get('expires_in')
    if not isinstance(token, str) or not token or type(expires) is not int or expires <= 600:
        raise PlatformUnavailable('微信令牌响应异常。')
    return token, timezone.now() + timedelta(seconds=expires)


def component_token():
    row = _config()
    credentials = _credentials(row)
    cached = _safe_token('component-access-token', row.encrypted_access_token,
                         row.access_token_expires_at)
    if cached:
        return cached
    if not row.encrypted_ticket:
        raise PlatformStateError('尚未收到微信第三方平台验证票据。')
    ticket = unseal('component-ticket', row.encrypted_ticket)
    value = call('POST', '/cgi-bin/component/api_component_token', payload={
        'component_appid': row.component_app_id,
        'component_appsecret': credentials['componentAppSecret'],
        'component_verify_ticket': ticket,
    })
    token, expires_at = _token_result(value, 'component_access_token')
    with transaction.atomic():
        current = ComponentConfig.objects.select_for_update().get(pk=1)
        if current.revision != row.revision or current.encrypted_ticket != row.encrypted_ticket:
            raise PlatformStateError('第三方平台配置已变化，请重试读取。')
        newer = _safe_token('component-access-token', current.encrypted_access_token,
                            current.access_token_expires_at)
        if newer:
            return newer
        current.encrypted_access_token = seal('component-access-token', token)
        current.access_token_expires_at = expires_at
        current.save(update_fields=['encrypted_access_token', 'access_token_expires_at'])
    return token


def _scopes(info):
    try:
        raw = info.get('func_info', [])
        if not isinstance(raw, list):
            raise ValueError()
        values = [item['funcscope_category']['id'] for item in raw]
        if any(type(value) is not int for value in values):
            raise ValueError()
        return sorted(set(values))
    except (TypeError, KeyError, ValueError):
        raise PlatformUnavailable('微信授权信息格式异常。') from None


def _save_grant(config, target, info, *, event_time=0):
    if not isinstance(info, dict) or info.get('authorizer_appid') != target:
        raise PlatformStateError('微信授权账号与本次目标小程序不一致。')
    refresh = info.get('authorizer_refresh_token')
    access = info.get('authorizer_access_token')
    scopes = _scopes(info)
    if not isinstance(refresh, str) or not refresh:
        raise PlatformStateError('微信未返回授权刷新令牌，请重新授权。')
    encrypted_refresh = seal('authorizer-refresh-token', refresh)
    encrypted_access = seal('authorizer-access-token', access) if isinstance(access, str) and access else ''
    expires = info.get('expires_in')
    access_expires = (timezone.now() + timedelta(seconds=expires)
                      if type(expires) is int and expires > 0 and encrypted_access else None)
    with transaction.atomic():
        current_config = ComponentConfig.objects.select_for_update().get(pk=1)
        if current_config.revision != config.revision:
            raise PlatformStateError('第三方平台配置已变化，请重新授权。')
        grant, _ = AuthorizerGrant.objects.select_for_update().get_or_create(
            authorizer_app_id=target, defaults={'component_app_id': config.component_app_id})
        if grant.last_event_time > event_time:
            raise PlatformStateError('已有更新的授权变更事件，请重新检查。')
        grant.component_app_id = config.component_app_id
        grant.encrypted_refresh_token = encrypted_refresh
        grant.encrypted_access_token = encrypted_access
        grant.access_token_expires_at = access_expires
        grant.scope_ids = scopes
        grant.revoked_at = None
        grant.verified_at = None
        grant.account_status = None
        grant.last_event_time = event_time
        grant.revision += 1
        grant.save()
    return {'authorizerAppId': target, 'codeManagementGranted': 18 in scopes}


def begin_authorization(target_app_id, *, actor=None):
    if not isinstance(target_app_id, str) or not APP_ID.fullmatch(target_app_id):
        raise PlatformStateError('目标小程序 AppID 格式错误。')
    config = _config()
    _redirect_uri(config.redirect_uri)
    token = component_token()
    value = call('POST', '/cgi-bin/component/api_create_preauthcode', access_token=token,
                 payload={'component_appid': config.component_app_id})
    preauth = value.get('pre_auth_code')
    if not isinstance(preauth, str) or not preauth:
        raise PlatformUnavailable('微信预授权码响应异常。')
    state = secrets.token_urlsafe(32)
    AuthorizationIntent.objects.create(
        state_hash=hashlib.sha256(state.encode()).hexdigest(), target_app_id=target_app_id,
        component_revision=config.revision, actor=actor,
        expires_at=timezone.now() + timedelta(minutes=10))
    callback = config.redirect_uri + '?' + urlencode({'state': state})
    url = 'https://mp.weixin.qq.com/cgi-bin/componentloginpage?' + urlencode({
        'component_appid': config.component_app_id, 'pre_auth_code': preauth,
        'redirect_uri': callback, 'auth_type': 2, 'biz_appid': target_app_id,
        'category_id_list': 18,
    })
    return {'url': url, 'state': state, 'targetAppId': target_app_id}


def finish_authorization(state, authorization_code):
    if (not isinstance(state, str) or not (32 <= len(state) <= 128)
            or not isinstance(authorization_code, str) or not (1 <= len(authorization_code) <= 512)):
        raise PlatformStateError('授权回调参数错误。')
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    with transaction.atomic():
        intent = AuthorizationIntent.objects.select_for_update().filter(state_hash=state_hash).first()
        if intent is None or intent.consumed_at or intent.expires_at <= timezone.now():
            raise PlatformStateError('授权链接已过期或已经使用。')
        intent.consumed_at = timezone.now()
        intent.save(update_fields=['consumed_at'])
    config = _config()
    if config.revision != intent.component_revision:
        raise PlatformStateError('第三方平台配置已变化，请重新授权。')
    token = component_token()
    value = call('POST', '/cgi-bin/component/api_query_auth', access_token=token, payload={
        'component_appid': config.component_app_id, 'authorization_code': authorization_code,
    })
    return _save_grant(config, intent.target_app_id, value.get('authorization_info'),
                       event_time=int(intent.created_at.timestamp()))


def _grant(target):
    config = _config()
    grant = AuthorizerGrant.objects.filter(authorizer_app_id=target).first()
    if (grant is None or grant.revoked_at or not grant.encrypted_refresh_token
            or grant.component_app_id != config.component_app_id):
        raise PlatformStateError('目标小程序尚未授权当前第三方平台。')
    return config, grant


def authorizer_token(target):
    config, grant = _grant(target)
    cached = _safe_token('authorizer-access-token', grant.encrypted_access_token,
                         grant.access_token_expires_at)
    if cached:
        return cached
    token = component_token()
    refresh = unseal('authorizer-refresh-token', grant.encrypted_refresh_token)
    value = call('POST', '/cgi-bin/component/api_authorizer_token', access_token=token, payload={
        'component_appid': config.component_app_id, 'authorizer_appid': target,
        'authorizer_refresh_token': refresh,
    })
    access, expires = _token_result(value, 'authorizer_access_token')
    rotated = value.get('authorizer_refresh_token', refresh)
    if not isinstance(rotated, str) or not rotated:
        raise PlatformUnavailable('微信授权令牌响应异常。')
    with transaction.atomic():
        live_config = ComponentConfig.objects.select_for_update().get(pk=1)
        if live_config.revision != config.revision or live_config.component_app_id != config.component_app_id:
            raise PlatformStateError('第三方平台配置已变化，请重新检查。')
        current = AuthorizerGrant.objects.select_for_update().get(pk=grant.pk)
        if (current.revision != grant.revision or current.revoked_at
                or current.component_app_id != config.component_app_id):
            raise PlatformStateError('目标小程序授权已变化，请重新检查。')
        newer = _safe_token('authorizer-access-token', current.encrypted_access_token,
                            current.access_token_expires_at)
        if newer:
            return newer
        current.encrypted_access_token = seal('authorizer-access-token', access)
        current.access_token_expires_at = expires
        current.encrypted_refresh_token = seal('authorizer-refresh-token', rotated)
        current.save(update_fields=['encrypted_access_token', 'access_token_expires_at',
                                    'encrypted_refresh_token'])
    return access


def authorization_status(target_app_id):
    """Live check; only a matching, normal mini-program with scope 18 can pass."""
    if not isinstance(target_app_id, str) or not APP_ID.fullmatch(target_app_id):
        return {'status': 'BLOCKED', 'code': 'INVALID_APP_ID', 'detail': '目标小程序 AppID 格式错误。'}
    try:
        config, grant = _grant(target_app_id)
    except PlatformStateError as exc:
        return {'status': 'BLOCKED', 'code': 'NOT_AUTHORIZED', 'detail': str(exc)}
    try:
        token = component_token()
        value = call('POST', '/cgi-bin/component/api_get_authorizer_info', access_token=token,
                     payload={'component_appid': config.component_app_id,
                              'authorizer_appid': target_app_id})
        info = value.get('authorizer_info')
        auth = value.get('authorization_info')
        if not isinstance(info, dict) or not isinstance(auth, dict):
            raise PlatformUnavailable('微信授权详情格式异常。')
        if auth.get('authorizer_appid') != target_app_id:
            return {'status': 'BLOCKED', 'code': 'APP_ID_MISMATCH', 'detail': '微信授权账号与目标 AppID 不一致。'}
        scopes = _scopes(auth)
        if 'MiniProgramInfo' not in info or not isinstance(info['MiniProgramInfo'], dict):
            return {'status': 'BLOCKED', 'code': 'NOT_MINIPROGRAM', 'detail': '授权账号不是小程序。'}
        if 18 not in scopes:
            return {'status': 'BLOCKED', 'code': 'CODE_SCOPE_MISSING', 'detail': '未授权代码管理权限集 18。'}
        if info.get('account_status') != 1:
            return {'status': 'UNVERIFIED' if info.get('account_status') is None else 'BLOCKED',
                    'code': 'ACCOUNT_STATUS', 'detail': '目标小程序账号状态不正常或无法确认。'}
        with transaction.atomic():
            live_config = ComponentConfig.objects.select_for_update().get(pk=1)
            if live_config.revision != config.revision or live_config.component_app_id != config.component_app_id:
                raise PlatformStateError('第三方平台配置已变化。')
            current = AuthorizerGrant.objects.select_for_update().get(pk=grant.pk)
            if current.revision != grant.revision or current.revoked_at:
                raise PlatformStateError('授权状态已变化。')
            current.scope_ids = scopes
            current.account_status = 1
            current.verified_at = timezone.now()
            current.save(update_fields=['scope_ids', 'account_status', 'verified_at'])
        return {'status': 'PASS', 'code': 'AUTHORIZED', 'detail': '小程序授权与代码管理权限已通过微信接口验证。'}
    except (CredentialsUnavailable, PlatformUnavailable, PlatformRejected, PlatformStateError):
        return {'status': 'UNVERIFIED', 'code': 'PLATFORM_UNAVAILABLE',
                'detail': '当前无法向微信核验授权或令牌，请稍后重试。'}


def receive_encrypted_event(raw, *, signature, timestamp, nonce):
    if (not isinstance(timestamp, str) or not timestamp.isdigit()
            or abs(int(timestamp) - int(timezone.now().timestamp())) > 1200):
        raise PlatformStateError('消息时间戳不在允许范围内。')
    config = _config()
    credentials = _credentials(config)
    fields = decode_callback(raw, signature=signature, timestamp=timestamp, nonce=nonce,
                             token=credentials['messageToken'], encoding_key=credentials['encodingAesKey'],
                             component_app_id=config.component_app_id)
    if fields.get('AppId') != config.component_app_id:
        raise PlatformStateError('事件平台标识不一致。')
    created = fields.get('CreateTime', '')
    if not created.isdigit() or abs(int(created) - int(timezone.now().timestamp())) > 1200:
        raise PlatformStateError('消息创建时间不在允许范围内。')
    event_time = int(created)
    event = fields.get('InfoType')
    if event == 'component_verify_ticket':
        ticket = fields.get('ComponentVerifyTicket')
        if not ticket or len(ticket) > 512:
            raise PlatformStateError('验证票据缺失。')
        with transaction.atomic():
            row = ComponentConfig.objects.select_for_update().get(pk=1)
            if row.revision != config.revision:
                raise PlatformStateError('平台配置已变化。')
            if row.ticket_event_time > event_time:
                return event
            row.encrypted_ticket = seal('component-ticket', ticket)
            row.ticket_received_at = timezone.now()
            row.ticket_event_time = event_time
            row.save(update_fields=['encrypted_ticket', 'ticket_received_at', 'ticket_event_time'])
        return 'component_verify_ticket'
    target = fields.get('AuthorizerAppid', '')
    if not APP_ID.fullmatch(target):
        raise PlatformStateError('授权事件目标账号错误。')
    if event == 'unauthorized':
        with transaction.atomic():
            grant = AuthorizerGrant.objects.select_for_update().filter(authorizer_app_id=target,
                                                                       component_app_id=config.component_app_id).first()
            if grant:
                if grant.last_event_time > event_time:
                    return event
                grant.revoked_at = timezone.now()
                grant.encrypted_refresh_token = ''
                grant.encrypted_access_token = ''
                grant.access_token_expires_at = None
                grant.verified_at = None
                grant.last_event_time = event_time
                grant.revision += 1
                grant.save()
        return event
    if event in ('authorized', 'updateauthorized'):
        code = fields.get('AuthorizationCode')
        if not code or len(code) > 512:
            raise PlatformStateError('授权码缺失。')
        token = component_token()
        value = call('POST', '/cgi-bin/component/api_query_auth', access_token=token, payload={
            'component_appid': config.component_app_id, 'authorization_code': code,
        })
        _save_grant(config, target, value.get('authorization_info'), event_time=event_time)
        return event
    raise PlatformStateError('未知授权事件。')


def _authorized(target):
    checked = authorization_status(target)
    if checked['status'] != 'PASS':
        raise PlatformStateError(checked['detail'])
    return authorizer_token(target)


def submit_audit(target_app_id, *, item_list, version_desc):
    """Single submit attempt. Caller must persist ATTEMPT before entering."""
    if (not isinstance(item_list, list) or not (1 <= len(item_list) <= 5)
            or not isinstance(version_desc, str) or len(version_desc) > 200):
        raise PlatformStateError('审核资料格式错误。')
    for item in item_list:
        if (not isinstance(item, dict) or not all(item.get(field)
                for field in ('first_class', 'second_class', 'first_id', 'second_id'))):
            raise PlatformStateError('审核类目信息不完整。')
    token = _authorized(target_app_id)
    value = call('POST', '/wxa/submit_audit', access_token=token,
                 payload={'item_list': item_list, 'version_desc': version_desc})
    return _audit_id(value.get('auditid'))


def _audit_id(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and 0 < len(value) <= 20:
        number = int(value)
        if number > 0:
            return number
    raise PlatformUnavailable('微信审核单号响应异常，结果需要人工核对。')


def get_category(target_app_id):
    """Audit category choices under permission set 18 (not category-management 30)."""
    token = authorizer_token(target_app_id)
    value = call('GET', '/wxa/get_category', access_token=token)
    categories = value.get('category_list')
    if not isinstance(categories, list):
        raise PlatformUnavailable('微信审核类目响应异常。')
    return [item for item in categories if isinstance(item, dict)]


def get_audit_status(target_app_id, auditid):
    if type(auditid) is not int or auditid <= 0:
        raise PlatformStateError('审核单号格式错误。')
    token = authorizer_token(target_app_id)
    value = call('POST', '/wxa/get_auditstatus', access_token=token, payload={'auditid': auditid})
    status = value.get('status')
    if type(status) is not int or status not in (0, 1, 2, 3, 4):
        raise PlatformUnavailable('微信审核状态响应异常。')
    return {'auditid': auditid, 'status': status, 'reason': value.get('reason', '')}


def get_latest_auditstatus(target_app_id):
    token = authorizer_token(target_app_id)
    value = call('GET', '/wxa/get_latest_auditstatus', access_token=token)
    if type(value.get('status')) is not int or value['status'] not in (0, 1, 2, 3, 4):
        raise PlatformUnavailable('微信最新审核单响应异常。')
    user_version = value.get('user_version')
    if user_version is not None and (not isinstance(user_version, str) or len(user_version) > 64):
        raise PlatformUnavailable('微信最新审核单响应异常。')
    return {'auditid': _audit_id(value.get('auditid')), 'status': value['status'],
            'userVersion': user_version or '', 'reason': value.get('reason', '')}


def release_approved(target_app_id, *, auditid, user_version):
    """Guard last-approved target; release API itself cannot bind an audit id."""
    if not isinstance(user_version, str) or not user_version or len(user_version) > 64:
        raise PlatformStateError('版本号格式错误。')
    token = _authorized(target_app_id)
    status = get_audit_status(target_app_id, auditid)
    if status['status'] != 0:
        raise PlatformStateError('该审核单尚未通过。')
    latest = get_latest_auditstatus(target_app_id)
    if (latest['auditid'] != auditid or latest['status'] != 0 or
            (latest['userVersion'] and latest['userVersion'] != user_version)):
        raise PlatformStateError('微信最新审核通过的版本与本次发布任务不一致。')
    call('POST', '/wxa/release', access_token=token, payload={})
    return {'auditid': auditid, 'userVersion': user_version, 'status': 'RELEASE_REQUESTED'}
