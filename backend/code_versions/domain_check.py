"""Selected immutable package domain comparison; no WeChat configuration writes."""
import json
import uuid
import zipfile

from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_cookie

from accounts.read_rate_limit import read_rate_limit
from accounts.security import require, require_live
from catalog.storage import LocalStorage
from common.http import error, method, parse_json_object, response
from wechat_integration.credentials import CredentialsUnavailable, effective_credentials
from wechat_integration.domain_probe import check_request_domains, https_origin

from .models import CodeVersion
from .release_service import API_URL, APP_ID
from .service import object_status

DETAILS = {
    'OK': '微信已配置此代码包 API 所需的 request 合法域名；其他资源域名与真机访问仍需单独验证。',
    'REQUEST_DOMAIN_MISSING': '微信尚未配置所需 request 合法域名，请在微信公众平台补充后重新检查。',
    'CREDENTIALS_NOT_CONFIGURED': '请先配置有效的 AppID 和 AppSecret，再检查域名。',
    'CREDENTIALS_UNAVAILABLE': '小程序凭据暂不可读取，请检查后台配置。',
    'PACKAGE_UNAVAILABLE': '代码包不可读取或摘要不一致，无法确认所需域名。',
    'APP_ID_MISMATCH': '所选代码包的 AppID 与后台配置不一致。',
    'API_URL_UNVERIFIED': '无法从所选代码包确认 API 地址。',
    'API_URL_INVALID': '代码包 API 地址必须使用有效的公网 HTTPS 域名。',
    'INVALID_CREDENTIALS': '微信未接受当前 AppID 或 AppSecret，请在小程序接入中核对。',
    'IP_NOT_ALLOWED': '微信拒绝当前服务器的接口调用 IP；请检查接口调用白名单，这与代码上传白名单分别配置。',
    'ADMIN_CONFIRMATION_REQUIRED': '微信要求管理员确认接口调用，请在微信公众平台完成确认。',
    'ADMIN_REJECTED': '微信管理员未允许当前接口调用，请在微信公众平台检查。',
    'API_PERMISSION_DENIED': '微信未允许当前账号查询域名，暂时无法验证，请在微信公众平台核对。',
    'PLATFORM_RATE_LIMITED': '微信接口调用频率受限，请稍后重新检查。',
    'PLATFORM_UNAVAILABLE': '暂时无法连接微信，域名配置尚未验证，请稍后重试。',
    'PLATFORM_ERROR': '微信返回结果未能完成域名验证，请稍后重试。',
    'CONFIGURATION_CHANGED': '检查期间小程序凭据已变化，请重新检查。',
}


def _package_origin(version, app_id):
    if not version or object_status(version, verify_digest=True) != 'READY':
        return 'PACKAGE_UNAVAILABLE', None
    try:
        with zipfile.ZipFile(LocalStorage().path(version.object_key)) as archive:
            if len(archive.infolist()) > 10000:
                raise ValueError()
            for name in ('project.config.json', 'app.js'):
                info = archive.getinfo(name)
                if info.file_size > 16384 or info.compress_size > 16384:
                    raise ValueError()
            with archive.open('project.config.json') as stream:
                config = json.loads(stream.read(16385).decode('utf-8'))
            with archive.open('app.js') as stream:
                script = stream.read(16385).decode('utf-8')
    except (OSError, ValueError, KeyError, UnicodeError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return 'PACKAGE_UNAVAILABLE', None
    if not isinstance(config, dict) or config.get('appid') != app_id:
        return 'APP_ID_MISMATCH', None
    matches = API_URL.findall(script)
    if len(matches) != 1:
        return 'API_URL_UNVERIFIED', None
    try:
        return 'OK', https_origin(matches[0])
    except ValueError:
        return 'API_URL_INVALID', None


def _result(version, app_id, status, code, *, required=(), configured=(), missing=(), platform_code=None):
    return {'versionId': str(version.pk) if version else None, 'appId': app_id,
            'checkedAt': timezone.now().isoformat(), 'status': status, 'code': code,
            'detail': DETAILS[code], 'requiredRequestDomains': list(required),
            'configuredRequestDomains': list(configured), 'missingRequestDomains': list(missing),
            'platformErrorCode': platform_code}


def _check(version, snapshot):
    if not APP_ID.fullmatch(snapshot.app_id) or not snapshot.secret:
        return _result(version, snapshot.app_id, 'BLOCKED', 'CREDENTIALS_NOT_CONFIGURED')
    code, origin = _package_origin(version, snapshot.app_id)
    if code != 'OK':
        status = 'UNVERIFIED' if code == 'API_URL_UNVERIFIED' else 'BLOCKED'
        return _result(version, snapshot.app_id, status, code)
    platform = check_request_domains(snapshot)
    if platform.status != 'PASS':
        return _result(version, snapshot.app_id, platform.status, platform.code,
                       required=[origin], platform_code=platform.platform_error_code)
    missing = [] if origin in platform.request_domains else [origin]
    return _result(version, snapshot.app_id, 'BLOCKED' if missing else 'PASS',
                   'REQUEST_DOMAIN_MISSING' if missing else 'OK', required=[origin],
                   configured=platform.request_domains, missing=missing)


@cache_control(private=True, no_store=True)
@vary_on_cookie
def domain_check_view(request):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = require(request, 'code.version.read')
    if bad:
        return bad
    try:
        body = parse_json_object(request, max_bytes=1024)
        if request.GET or set(body) - {'versionId'}:
            raise ValueError()
        version_id = uuid.UUID(body['versionId']) if isinstance(body.get('versionId'), str) else None
        if 'versionId' in body and version_id is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return error(request, 400, 'VALIDATION_FAILED', '请指定有效代码版本，或使用最新版本。')
    bad = read_rate_limit(request, actor, 'code.release')
    if bad:
        return bad
    versions = CodeVersion.objects
    version = versions.filter(pk=version_id).first() if version_id else versions.order_by('-created_at', '-id').first()
    if version_id and not version:
        return error(request, 404, 'NOT_FOUND', '代码版本不存在。')
    try:
        snapshot = effective_credentials()
        result = _check(version, snapshot)
        _, bad = require_live(request, 'code.version.read')
        if bad:
            return bad
        if effective_credentials() != snapshot:
            result = _result(version, snapshot.app_id, 'UNVERIFIED', 'CONFIGURATION_CHANGED')
    except CredentialsUnavailable:
        result = _result(version, None, 'BLOCKED', 'CREDENTIALS_UNAVAILABLE')
    return response(request, result)
