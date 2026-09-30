"""Credential-only platform check. Never creates identities or persists access tokens."""
import json
import ssl
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

ENDPOINT = 'https://api.weixin.qq.com/cgi-bin/stable_token'
MAX_BYTES = 8192


@dataclass(frozen=True)
class ProbeResult:
    status: str
    code: str


@dataclass(frozen=True)
class AccessTokenResult:
    status: str
    code: str
    token: str = field(default='', repr=False)
    platform_error_code: int | None = None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _transport(payload):
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    request = Request(ENDPOINT, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with opener.open(request, timeout=5) as response:
        if response.status != 200:
            raise ValueError()
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError()
    return body


def acquire_access_token(snapshot):
    """Use a stable token without forcing refresh, persisting it, or exposing it in repr."""
    try:
        raw = _transport({'grant_type': 'client_credential', 'appid': snapshot.app_id,
                          'secret': snapshot.secret, 'force_refresh': False})
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeError):
        return AccessTokenResult('UNAVAILABLE', 'PLATFORM_UNAVAILABLE')
    code = result.get('errcode', 0)
    if type(code) is not int:
        return AccessTokenResult('UNAVAILABLE', 'PLATFORM_ERROR')
    if code in {40013, 40125, 40001}:
        return AccessTokenResult('FAILED', 'INVALID_CREDENTIALS', platform_error_code=code)
    if code == 40164:
        return AccessTokenResult('FAILED', 'IP_NOT_ALLOWED', platform_error_code=code)
    if code == 89503:
        return AccessTokenResult('FAILED', 'ADMIN_CONFIRMATION_REQUIRED', platform_error_code=code)
    if code in {89506, 89507}:
        return AccessTokenResult('FAILED', 'ADMIN_REJECTED', platform_error_code=code)
    if code in {45009, 45011}:
        return AccessTokenResult('UNAVAILABLE', 'PLATFORM_RATE_LIMITED', platform_error_code=code)
    if code:
        return AccessTokenResult('UNAVAILABLE', 'PLATFORM_ERROR', platform_error_code=code)
    token, expiry = result.get('access_token'), result.get('expires_in')
    if (not isinstance(token, str) or not token or len(token) > 2048
            or type(expiry) is not int or expiry <= 0):
        return AccessTokenResult('UNAVAILABLE', 'PLATFORM_ERROR')
    return AccessTokenResult('SUCCESS', 'OK', token)


def check_credentials(snapshot):
    result = acquire_access_token(snapshot)
    return ProbeResult(result.status, result.code)
