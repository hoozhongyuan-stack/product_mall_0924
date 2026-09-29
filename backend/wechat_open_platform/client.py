"""Fixed-host, bounded WeChat API transport without redirects or automatic retries."""
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class PlatformUnavailable(RuntimeError):
    pass


class PlatformRejected(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'微信接口拒绝请求（{code}）。')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
_MAX_BYTES = 131072


def _transport(method, path, *, access_token='', payload=None):
    url = 'https://api.weixin.qq.com' + path
    if access_token:
        url += '?' + urlencode({'access_token': access_token})
    data = json.dumps(payload, ensure_ascii=False).encode() if method == 'POST' else None
    request = Request(url, data=data, headers={'Content-Type': 'application/json'}, method=method)
    try:
        with _OPENER.open(request, timeout=8) as reply:
            if reply.status != 200:
                raise PlatformUnavailable('微信接口暂不可用。')
            raw = reply.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError):
        raise PlatformUnavailable('微信接口暂不可用，结果需要人工核对。') from None


def call(method, path, *, access_token='', payload=None, transport=None):
    try:
        value = (transport or _transport)(method, path, access_token=access_token, payload=payload)
    except PlatformUnavailable:
        raise
    except (TimeoutError, OSError, ValueError, KeyError, TypeError):
        raise PlatformUnavailable('微信接口暂不可用，结果需要人工核对。') from None
    if not isinstance(value, dict):
        raise PlatformUnavailable('微信接口返回异常。')
    code = value.get('errcode', 0)
    if type(code) is not int:
        raise PlatformUnavailable('微信接口返回异常。')
    if code:
        raise PlatformRejected(code)
    return value
