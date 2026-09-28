"""Fail-closed loading of private, deployment-owned WeChat payment material."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from urllib.parse import urlsplit

from django.conf import settings


class WechatGatewayError(ValueError):
    def __init__(self, message, code="WECHAT_INVALID", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class WechatConfig:
    app_id: str
    merchant_id: str
    merchant_serial: str
    notify_url: str
    api_v3_key: bytes = field(repr=False)
    private_key_pem: bytes = field(repr=False)
    platform_keys: tuple = field(repr=False)
    timeout_seconds: int = 8
    replay_window_seconds: int = 300


def validate_config(config):
    if (not isinstance(config, WechatConfig) or
            any(not isinstance(value, str) for value in
                (config.app_id, config.merchant_id, config.merchant_serial, config.notify_url)) or
            not isinstance(config.api_v3_key, bytes) or not isinstance(config.private_key_pem, bytes) or
            not isinstance(config.platform_keys, tuple) or
            any(not isinstance(entry, tuple) or len(entry) != 2 or
                not isinstance(entry[0], str) or not isinstance(entry[1], bytes) for entry in config.platform_keys)):
        raise WechatGatewayError("微信支付配置不完整或不正确。", "WECHAT_NOT_CONFIGURED", 503)
    url = urlsplit(config.notify_url)
    key_ids = [entry[0] for entry in config.platform_keys]
    if (not re.fullmatch(r"wx[A-Za-z0-9]{1,30}", config.app_id) or
            not re.fullmatch(r"[0-9]{1,32}", config.merchant_id) or
            not re.fullmatch(r"[A-Fa-f0-9]{1,64}", config.merchant_serial) or
            len(config.api_v3_key) != 32 or
            not config.private_key_pem or not key_ids or len(set(key_ids)) != len(key_ids) or
            any(not re.fullmatch(r"(?:PUB_KEY_ID_[0-9]+|[A-Fa-f0-9]{1,64})", key) for key in key_ids) or
            url.scheme != "https" or not url.hostname or url.username or url.password or
            url.query or url.fragment or not url.path or any(ord(c) < 32 for c in config.notify_url) or
            type(config.timeout_seconds) is not int or not 1 <= config.timeout_seconds <= 15 or
            type(config.replay_window_seconds) is not int or not 1 <= config.replay_window_seconds <= 300):
        raise WechatGatewayError("微信支付配置不完整或不正确。", "WECHAT_NOT_CONFIGURED", 503)
    return config


def _read_private_file(value, maximum=65536):
    if not isinstance(value, str) or not value:
        raise ValueError("missing file")
    with Path(value).open("rb") as stream:
        content = stream.read(maximum + 1)
    if not content or len(content) > maximum:
        raise ValueError("invalid file size")
    return content


def load_wechat_config():
    values = getattr(settings, "WECHAT_PAY", {})
    try:
        keys = values["PLATFORM_KEYS"]
        if not isinstance(keys, dict):
            raise ValueError("invalid key map")
        config = WechatConfig(
            app_id=values["APP_ID"], merchant_id=values["MERCHANT_ID"],
            merchant_serial=values["MERCHANT_SERIAL"], notify_url=values["NOTIFY_URL"],
            api_v3_key=_read_private_file(values["API_V3_KEY_FILE"], 32),
            private_key_pem=_read_private_file(values["PRIVATE_KEY_FILE"]),
            platform_keys=tuple((key_id, _read_private_file(path)) for key_id, path in keys.items()),
            timeout_seconds=values.get("TIMEOUT_SECONDS", 8),
            replay_window_seconds=values.get("REPLAY_WINDOW_SECONDS", 300),
        )
        return validate_config(config)
    except (KeyError, OSError, ValueError, TypeError, AttributeError):
        raise WechatGatewayError("微信支付尚未完成商户配置。", "WECHAT_NOT_CONFIGURED", 503) from None
