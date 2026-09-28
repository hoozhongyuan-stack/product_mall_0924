"""Public immutable credential snapshots. No cache, network, or fallback on corruption."""
import json
import hashlib
import hmac
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from .models import MiniProgramIntegration


class CredentialsUnavailable(ValueError):
    def __init__(self):
        super().__init__('小程序凭据暂不可用，请检查部署密钥与配置。')


@dataclass(frozen=True)
class CredentialSnapshot:
    app_id: str
    secret: str = field(repr=False)
    revision: int = 0
    source: str = 'ENV'


def cipher():
    try:
        path = getattr(settings, 'MALL_WECHAT_CREDENTIAL_KEY_FILE', '')
        if not path:
            raise ValueError()
        with Path(path).open('rb') as stream:
            key = stream.read(1025).strip()
        if len(key) != 44:
            raise ValueError()
        return Fernet(key)
    except (OSError, ValueError, TypeError):
        raise CredentialsUnavailable() from None


def key_available():
    try:
        cipher()
        return True
    except CredentialsUnavailable:
        return False


def integration_row(*, lock=False):
    query = MiniProgramIntegration.objects.select_for_update() if lock else MiniProgramIntegration.objects
    row = query.filter(pk=1).first()
    if row is None:
        raise CredentialsUnavailable()
    return row


def encrypt_credentials(app_id, secret):
    payload = {'purpose': 'wechat-mini-program', 'appId': app_id, 'appSecret': secret}
    return cipher().encrypt(json.dumps(payload, ensure_ascii=False).encode()).decode('ascii')


def effective_credentials(row=None):
    row = integration_row() if row is None else row
    if not row.managed:
        return CredentialSnapshot(getattr(settings, 'WECHAT_MINI_APP_ID', ''),
                                  getattr(settings, 'WECHAT_MINI_APP_SECRET', ''), row.revision)
    try:
        payload = json.loads(cipher().decrypt(row.encrypted_payload.encode()))
        if (not isinstance(payload, dict) or payload.get('purpose') != 'wechat-mini-program'
                or not isinstance(payload.get('appId'), str) or not payload['appId']
                or not isinstance(payload.get('appSecret'), str) or not payload['appSecret']):
            raise ValueError()
        return CredentialSnapshot(payload['appId'], payload['appSecret'], row.revision, 'MANAGED')
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        raise CredentialsUnavailable() from None


def effective_app_id():
    return effective_credentials().app_id


def snapshot_fingerprint(snapshot):
    """Private HMAC for invalidating ENV check history; never sent or audited."""
    payload = json.dumps([snapshot.source, snapshot.revision, snapshot.app_id, snapshot.secret]).encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def identity_binding():
    """The customer domain owns identity facts; this read never migrates members."""
    from customers.integration_access import member_app_ids
    app_ids = member_app_ids()
    return {'status': 'EMPTY' if not app_ids else 'BOUND' if len(app_ids) == 1 else 'MULTIPLE',
            'appId': app_ids[0] if len(app_ids) == 1 else None}
