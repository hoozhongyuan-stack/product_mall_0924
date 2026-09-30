"""Read normal-account request domains; never mutate domains or persist tokens."""
import ipaddress
import json
import re
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .probe import NoRedirect, acquire_access_token

ENDPOINT = 'https://api.weixin.qq.com/wxa/getwxadevinfo'
MAX_BYTES = 65536
LOCAL_SUFFIXES = ('.local', '.localhost', '.internal', '.lan', '.test', '.invalid')


@dataclass(frozen=True)
class DomainProbeResult:
    status: str
    code: str
    request_domains: tuple[str, ...] = ()
    platform_error_code: int | None = None


def https_origin(value, *, origin_only=False):
    """Canonical HTTPS origin; no credentials, local hosts, or IP addresses."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(char.isspace() for char in value):
        raise ValueError()
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower()
    if (parsed.scheme != 'https' or not host or len(host) > 253 or parsed.username or parsed.password
            or '.' not in host or host.endswith(LOCAL_SUFFIXES) or parsed.fragment
            or (origin_only and (parsed.path not in ('', '/') or parsed.query))):
        raise ValueError()
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
        raise ValueError()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        port = parsed.port
        if port == 0:
            raise ValueError()
        return f'https://{host}' + (f':{port}' if port and port != 443 else '')
    raise ValueError()


def _transport(token):
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    request = Request(ENDPOINT + '?' + urlencode({'access_token': token}),
                      data=b'{"action":"getserverdomain"}',
                      headers={'Content-Type': 'application/json'}, method='POST')
    with opener.open(request, timeout=5) as response:
        if response.status != 200:
            raise ValueError()
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError()
    return body


def _platform_failure(code):
    status, reason = {
        40001: ('BLOCKED', 'INVALID_CREDENTIALS'), 40013: ('BLOCKED', 'INVALID_CREDENTIALS'),
        40125: ('BLOCKED', 'INVALID_CREDENTIALS'), 40164: ('BLOCKED', 'IP_NOT_ALLOWED'),
        48001: ('UNVERIFIED', 'API_PERMISSION_DENIED'), 48002: ('UNVERIFIED', 'API_PERMISSION_DENIED'),
        86000: ('UNVERIFIED', 'API_PERMISSION_DENIED'),
        45009: ('UNVERIFIED', 'PLATFORM_RATE_LIMITED'), 45011: ('UNVERIFIED', 'PLATFORM_RATE_LIMITED'),
    }.get(code, ('UNVERIFIED', 'PLATFORM_ERROR'))
    return DomainProbeResult(status, reason, platform_error_code=code)


def check_request_domains(snapshot):
    token = acquire_access_token(snapshot)
    if token.status != 'SUCCESS':
        return DomainProbeResult('BLOCKED' if token.status == 'FAILED' else 'UNVERIFIED',
                                 token.code, platform_error_code=token.platform_error_code)
    try:
        result = json.loads(_transport(token.token))
        if not isinstance(result, dict) or type(result.get('errcode', 0)) is not int:
            raise ValueError()
        if result.get('errcode'):
            return _platform_failure(result['errcode'])
        domains = result.get('requestdomain')
        if not isinstance(domains, list) or len(domains) > 256:
            raise ValueError()
        return DomainProbeResult('PASS', 'OK', tuple(sorted({https_origin(item, origin_only=True) for item in domains})))
    except (HTTPError, URLError, TimeoutError, OSError):
        return DomainProbeResult('UNVERIFIED', 'PLATFORM_UNAVAILABLE')
    except (ValueError, UnicodeError):
        return DomainProbeResult('UNVERIFIED', 'PLATFORM_ERROR')
