"""Small configuration rules and safe DTOs; callers own transaction and permissions."""
import re
from django.conf import settings

from .credentials import (effective_credentials, encrypt_credentials, identity_binding, key_available,
                          snapshot_fingerprint)


class IntegrationError(ValueError):
    def __init__(self, message, code='VALIDATION_FAILED', status=400, details=None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def expected_revision(body):
    value = body.get('expectedRevision')
    if type(value) is not int or value < 0:
        raise IntegrationError('配置修订号不正确。')
    return value


def validate_save(body, snapshot):
    expected_revision(body)
    action = body.get('secretAction')
    required = {'expectedRevision', 'appId', 'secretAction'} | ({'appSecret'} if action == 'REPLACE' else set())
    app_id = body.get('appId')
    if (set(body) != required or not isinstance(action, str) or action not in {'KEEP', 'REPLACE'} or not isinstance(app_id, str)
            or re.fullmatch(r'wx[0-9a-fA-F]{16}', app_id) is None):
        raise IntegrationError('请填写有效的 AppID 和凭据保存方式。')
    secret = body.get('appSecret') if action == 'REPLACE' else snapshot.secret
    if action == 'KEEP' and (app_id != snapshot.app_id or not snapshot.secret):
        raise IntegrationError('首次配置或更换 AppID 时必须提供新的 AppSecret。')
    if (not isinstance(secret, str) or not 1 <= len(secret) <= 256 or not secret.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in secret)):
        raise IntegrationError('AppSecret 须为 1 至 256 个字符且不含控制符。')
    return app_id, secret


def check_revision(row, expected):
    if row.revision != expected:
        raise IntegrationError('配置已变化，请重新读取后确认。', 'REVISION_CONFLICT', 409,
                               [{'currentRevision': row.revision}])


def update_credentials(row, snapshot, app_id, secret, actor):
    binding = identity_binding()
    if ((binding['status'] == 'BOUND' and app_id != binding['appId'])
            or (binding['status'] == 'MULTIPLE' and app_id != snapshot.app_id)):
        raise IntegrationError('AppID 已关联会员身份，不能改为其他小程序。', 'APP_ID_LOCKED', 409)
    row.encrypted_payload = encrypt_credentials(app_id, secret)
    row.managed = True
    row.revision += 1
    row.updated_by = actor
    row.last_check_revision = row.last_check_at = None
    row.last_check_status = row.last_check_code = row.last_check_fingerprint = ''
    row.save()


def configuration_data(row):
    snapshot = effective_credentials(row)
    deployed_app = getattr(settings, 'WECHAT_PAY', {}).get('APP_ID', '')
    payment_status = ('NOT_CONFIGURED' if not deployed_app else
                      'MATCHED' if deployed_app == snapshot.app_id else 'MISMATCHED')
    check = None
    if (row.last_check_at and row.last_check_revision == row.revision
            and row.last_check_fingerprint == snapshot_fingerprint(snapshot)):
        check = {'revision': row.revision, 'status': row.last_check_status,
                 'code': row.last_check_code, 'checkedAt': row.last_check_at.isoformat()}
    return {'revision': row.revision, 'source': snapshot.source, 'appId': snapshot.app_id,
            'secretConfigured': bool(snapshot.secret), 'keyAvailable': key_available(),
            'identityBinding': identity_binding(), 'paymentAppIdStatus': payment_status,
            'notificationsStatus': 'NOT_VERIFIED', 'lastCheck': check}
