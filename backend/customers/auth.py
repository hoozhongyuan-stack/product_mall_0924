import hashlib
import hmac
import json
import re
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.utils import timezone
from wechat_integration.credentials import effective_credentials, CredentialsUnavailable

from .models import MemberSession, WechatLoginAttempt


class WechatExchangeUnavailable(Exception):
    pass


class InvalidWechatCode(Exception):
    pass


def configured_credentials():
    snapshot = effective_credentials()
    return snapshot.app_id, snapshot.secret


def exchange_code(code, *, credentials=None):
    """Exchange wx.login's one-use code server-side; session_key never leaves this call."""
    credentials = effective_credentials() if credentials is None else credentials
    app_id, secret = credentials.app_id, credentials.secret
    if not app_id or not secret:
        raise WechatExchangeUnavailable("微信登录尚未配置。")
    query = urlencode({"appid": app_id, "secret": secret, "js_code": code, "grant_type": "authorization_code"})
    try:
        with urlopen(f"https://api.weixin.qq.com/sns/jscode2session?{query}", timeout=5) as response:
            raw = response.read(4097)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise WechatExchangeUnavailable("微信身份校验暂时不可用，请重试。") from exc
    if len(raw) > 4096:
        raise WechatExchangeUnavailable("微信身份校验响应异常，请重试。")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WechatExchangeUnavailable("微信身份校验响应异常，请重试。") from exc
    if not isinstance(result, dict):
        raise WechatExchangeUnavailable("微信身份校验响应异常，请重试。")
    if result.get("errcode"):
        if result["errcode"] == -1:
            raise WechatExchangeUnavailable("微信身份校验暂时不可用，请重试。")
        raise InvalidWechatCode("微信登录凭证已失效，请重新登录。")
    openid = result.get("openid")
    if not isinstance(openid, str) or not 1 <= len(openid) <= 128:
        raise WechatExchangeUnavailable("微信身份校验响应异常，请重试。")
    return openid


def code_digest(app_id, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"{app_id}:{code}".encode(), hashlib.sha256).hexdigest()


def login_source_allowed(request):
    source = request.META.get("REMOTE_ADDR", "unknown")
    digest = hmac.new(settings.SECRET_KEY.encode(), source.encode(), hashlib.sha256).hexdigest()
    since = timezone.now() - timedelta(minutes=15)
    recent = WechatLoginAttempt.objects.filter(source_digest=digest, created_at__gte=since).count()
    if recent >= 30:
        return False
    WechatLoginAttempt.objects.create(source_digest=digest)
    return True


def matches_active_app(member):
    try:
        app_id = effective_credentials().app_id
        return bool(app_id and member.wechat_app_id == app_id)
    except CredentialsUnavailable:
        return False


def resolve_member(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{40,100})", header)
    if not match:
        return None
    digest = hashlib.sha256(match.group(1).encode()).hexdigest()
    session = MemberSession.objects.select_related("member", "member__grade").filter(
        token_digest=digest, revoked_at__isnull=True, expires_at__gt=timezone.now(),
        member__enabled=True,
    ).first()
    if not session or session.auth_version != session.member.auth_version:
        return None
    if not matches_active_app(session.member):
        return None
    return session.member


def require_member(request):
    from .views import failure

    member = resolve_member(request)
    if member is None:
        return None, failure(request, 401, "LOGIN_REQUIRED", "请先完成微信登录。")
    return member, None
