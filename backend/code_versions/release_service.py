"""Read-only local release checks and private CI-key handling.

No check in this module asserts a WeChat code upload, audit, or release.
"""
import json
import ipaddress
import re
import zipfile
from datetime import timedelta
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone

from wechat_integration.credentials import (CredentialsUnavailable, cipher, effective_credentials,
                                            integration_row, snapshot_fingerprint)

from wechat_open_platform.service import (PlatformStateError, authorization_status,
                                          component_settings)

from .models import CodeUploadKey, CodeVersion, DeveloperUploadKey
from .service import object_status
from catalog.storage import LocalStorage


APP_ID = re.compile(r'wx[0-9a-fA-F]{16}\Z')
API_URL = re.compile(r'apiBaseUrl\s*:\s*[\'\"]([^\'\"]+)[\'\"]')
PROBE_FRESHNESS = timedelta(hours=24)
LOCAL_HOST_SUFFIXES = ('.local', '.localhost', '.internal', '.lan', '.test', '.invalid')


class UploadKeyUnavailable(ValueError):
    pass


def validate_upload_key(value):
    if not isinstance(value, str):
        raise ValueError('请上传不超过 16 KiB 的代码上传私钥。')
    try:
        encoded = value.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError('代码上传私钥文本编码不正确。') from exc
    if not 1 <= len(encoded) <= 16384:
        raise ValueError('请上传不超过 16 KiB 的代码上传私钥。')
    try:
        parsed = serialization.load_pem_private_key(encoded, password=None)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError('代码上传私钥不是有效的 PEM RSA 私钥。') from exc
    if not isinstance(parsed, rsa.RSAPrivateKey):
        raise ValueError('代码上传私钥不是有效的 PEM RSA 私钥。')
    return value


def encrypt_upload_key(app_id, value):
    payload = {'purpose': 'wechat-mini-program-code-upload', 'appId': app_id, 'key': value}
    return cipher().encrypt(json.dumps(payload).encode()).decode('ascii')


def encrypt_developer_upload_key(app_id, value):
    payload = {'purpose': 'wechat-third-party-developer-code-upload', 'appId': app_id, 'key': value}
    return cipher().encrypt(json.dumps(payload).encode()).decode('ascii')


def _decrypt_upload_key(row, purpose):
    if not row.encrypted_payload:
        raise UploadKeyUnavailable()
    try:
        payload = json.loads(cipher().decrypt(row.encrypted_payload.encode()))
        if (not isinstance(payload, dict) or payload.get('purpose') != purpose
                or payload.get('appId') != row.app_id):
            raise ValueError()
        return validate_upload_key(payload.get('key'))
    except (CredentialsUnavailable, InvalidToken, ValueError, TypeError, UnicodeError) as exc:
        raise UploadKeyUnavailable() from exc


def decrypt_upload_key(row):
    return _decrypt_upload_key(row, 'wechat-mini-program-code-upload')


def decrypt_developer_upload_key(row):
    return _decrypt_upload_key(row, 'wechat-third-party-developer-code-upload')


def upload_key_data(row):
    return {'configured': bool(row.encrypted_payload), 'revision': row.revision,
            'appId': row.app_id or None}


def check(code, status, title, detail):
    return {'code': code, 'status': status, 'title': title, 'detail': detail}


def _package_config(version, app_id):
    if not version or object_status(version, verify_digest=True) != 'READY':
        return [check('SOURCE_PACKAGE', 'BLOCKED', '代码包', '当前没有完整可读取的不可变代码包。'),
                check('RELEASE_CONFIG', 'UNVERIFIED', '发布配置', '代码包不可读取，无法检查发布配置。')]
    try:
        path = LocalStorage().path(version.object_key)
        with zipfile.ZipFile(path) as archive:
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
    except (OSError, ValueError, KeyError, UnicodeError, RuntimeError, zipfile.BadZipFile,
            zipfile.LargeZipFile, json.JSONDecodeError):
        return [check('SOURCE_PACKAGE', 'BLOCKED', '代码包', '代码包中的必要配置无法安全读取。'),
                check('RELEASE_CONFIG', 'UNVERIFIED', '发布配置', '无法读取代码包配置。')]
    source = check('SOURCE_PACKAGE', 'PASS', '代码包', '不可变代码包摘要与本地私有文件一致。')
    package_app = config.get('appid') if isinstance(config, dict) else None
    if not app_id or package_app != app_id:
        release = check('RELEASE_CONFIG', 'BLOCKED', '发布配置', '代码包 AppID 与后台配置不一致。')
    else:
        match = API_URL.search(script)
        if not match:
            release = check('RELEASE_CONFIG', 'UNVERIFIED', '发布配置', '无法从代码包确认 API 地址。')
        elif not _public_https(match.group(1)):
            release = check('RELEASE_CONFIG', 'BLOCKED', '发布配置', '代码包 API 地址须为 HTTPS 且不能指向本机。')
        else:
            release = check('RELEASE_CONFIG', 'PASS', '发布配置',
                            '代码包 AppID 一致且 API 地址使用 HTTPS；微信合法域名及真机仍需单独核验。')
    return [source, release]


def _public_https(value):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').rstrip('.').lower()
        if (parsed.scheme != 'https' or not host or parsed.username or parsed.password
                or host == 'localhost' or host.endswith(LOCAL_HOST_SUFFIXES)):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return '.' in host
    except ValueError:
        return False


def readiness_data():
    try:
        integration = integration_row()
        snapshot = effective_credentials(integration)
        app_id = snapshot.app_id
        app_check = check('APP_ID', 'PASS' if APP_ID.fullmatch(app_id) else 'BLOCKED',
                          'AppID', '已配置小程序 AppID。' if APP_ID.fullmatch(app_id) else '请配置有效的小程序 AppID。')
        if not snapshot.secret:
            secret_check = check('APP_SECRET', 'BLOCKED', 'AppSecret', '请配置 AppSecret。')
        elif (integration.last_check_at and integration.last_check_revision == integration.revision
              and integration.last_check_fingerprint == snapshot_fingerprint(snapshot)):
            age = timezone.now() - integration.last_check_at
            checked_at = timezone.localtime(integration.last_check_at).strftime('%Y-%m-%d %H:%M')
            if not timedelta(0) <= age <= PROBE_FRESHNESS:
                secret_check = check('APP_SECRET', 'UNVERIFIED', 'AppSecret',
                                     f'上次检测：{checked_at}，已超过 24 小时或时间异常，请重新检测。')
            else:
                status = ('PASS' if integration.last_check_status == 'SUCCESS' else
                          'BLOCKED' if integration.last_check_status == 'FAILED' else 'UNVERIFIED')
                detail = ('当前凭据已通过微信接口检测。' if status == 'PASS' else
                          '当前凭据未通过最近一次微信接口检测。' if status == 'BLOCKED' else
                          '最近一次微信接口检测未得出结论，请稍后重试。')
                secret_check = check('APP_SECRET', status, 'AppSecret', f'{detail} 上次检测：{checked_at}。')
        else:
            secret_check = check('APP_SECRET', 'UNVERIFIED', 'AppSecret', '已配置，尚未完成当前凭据的微信接口检测。')
    except CredentialsUnavailable:
        app_id = None
        app_check = check('APP_ID', 'BLOCKED', 'AppID', '小程序凭据无法读取，请检查部署密钥。')
        secret_check = check('APP_SECRET', 'BLOCKED', 'AppSecret', '小程序凭据无法读取或解密。')
    version = CodeVersion.objects.order_by('-created_at', '-id').first()
    key_row = CodeUploadKey.objects.filter(pk=1).first() or CodeUploadKey()
    if not key_row.encrypted_payload:
        key_check = check('UPLOAD_KEY', 'BLOCKED', '代码上传密钥', '请上传微信小程序代码上传私钥。')
    elif key_row.app_id != app_id:
        key_check = check('UPLOAD_KEY', 'BLOCKED', '代码上传密钥', '密钥绑定的 AppID 与当前配置不一致。')
    else:
        try:
            decrypt_upload_key(key_row)
            key_check = check('UPLOAD_KEY', 'PASS', '代码上传密钥', '已加密保存且当前可解密；尚未验证微信平台接收。')
        except UploadKeyUnavailable:
            key_check = check('UPLOAD_KEY', 'BLOCKED', '代码上传密钥', '密钥无法解密或格式无效，请重新上传。')
    try:
        platform = component_settings()
        configured = platform['configured'] and platform['ticketReceived']
        platform_check = check('PLATFORM_INTEGRATION', 'PASS' if configured else 'BLOCKED',
            '微信第三方平台接入', '组件凭据和微信验证票据已配置。' if configured else
            '请配置第三方平台组件凭据，并确认微信验证票据回调成功。')
        developer_id = platform['developerAppId']
        developer_row = DeveloperUploadKey.objects.filter(pk=1).first() or DeveloperUploadKey()
        if developer_id and developer_row.app_id == developer_id and developer_row.encrypted_payload:
            try:
                decrypt_developer_upload_key(developer_row)
                developer_status = 'PASS'
            except UploadKeyUnavailable:
                developer_status = 'BLOCKED'
        else:
            developer_status = 'BLOCKED'
        developer_check = check('DEVELOPER_UPLOAD_KEY', developer_status,
            '开发小程序上传密钥', '开发小程序密钥已加密保存且可解密。' if developer_status == 'PASS' else
            '请上传与第三方平台绑定的开发小程序 AppID 对应的代码上传密钥。')
        authorized = authorization_status(app_id) if configured and app_id else {
            'status': 'BLOCKED' if not configured else 'UNVERIFIED',
            'detail': '第三方平台尚未就绪，无法核验目标小程序授权。'}
        auth_check = check('THIRD_PARTY_AUTH', authorized['status'], '微信第三方授权',
                           authorized['detail'])
    except (PlatformStateError, CredentialsUnavailable):
        platform_check = check('PLATFORM_INTEGRATION', 'BLOCKED', '微信第三方平台接入',
                               '第三方平台组件凭据尚未配置或无法读取。')
        developer_check = check('DEVELOPER_UPLOAD_KEY', 'BLOCKED', '开发小程序上传密钥',
                                '请先配置第三方平台及开发小程序密钥。')
        auth_check = check('THIRD_PARTY_AUTH', 'UNVERIFIED', '微信第三方授权',
                           '第三方平台尚未就绪，无法核验授权。')
        developer_row = DeveloperUploadKey()
        developer_id = None
    checks = [app_check, secret_check, *_package_config(version, app_id), key_check,
              platform_check, developer_check, auth_check]
    return {'appId': app_id, 'versionId': str(version.pk) if version else None,
            'checks': checks, 'uploadKey': upload_key_data(key_row),
            'developerAppId': developer_id, 'developerUploadKey': upload_key_data(developer_row)}
