"""APIv3 RSA signatures and AES-GCM notifications; never trust unsigned JSON.

Transport is injectable for development tests. The production transport only
connects to the fixed HTTPS provider host and does not follow redirects.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import secrets
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .wechat_config import WechatGatewayError, load_wechat_config, validate_config

PROVIDER_HOST = "https://api.mch.weixin.qq.com"
MAX_BODY_BYTES = 65536


@dataclass(frozen=True)
class GatewayResponse:
    status: int
    headers: dict
    body: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _transport(method, path, headers, body, timeout):
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    request = Request(PROVIDER_HOST + path, data=body if method != "GET" else None,
                      headers=headers, method=method)
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, OSError):
        raise WechatGatewayError("微信支付服务暂时不可用，请稍后查询订单状态。", "WECHAT_UNAVAILABLE", 503) from None
    try:
        with response:
            data = response.read(MAX_BODY_BYTES + 1)
            if len(data) > MAX_BODY_BYTES:
                raise WechatGatewayError("微信支付响应过大。", "WECHAT_RESPONSE_INVALID", 502)
            return GatewayResponse(response.status, dict(response.headers), data)
    except (TimeoutError, OSError):
        raise WechatGatewayError("微信支付服务暂时不可用，请稍后查询订单状态。", "WECHAT_UNAVAILABLE", 503) from None


def _object(body):
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise WechatGatewayError("微信支付报文格式不正确。", "WECHAT_RESPONSE_INVALID", 502) from None
    if not isinstance(value, dict):
        raise WechatGatewayError("微信支付报文格式不正确。", "WECHAT_RESPONSE_INVALID", 502)
    return value


def _text(value, limit=128):
    return isinstance(value, str) and 0 < len(value) <= limit and value == value.strip() and all(ord(c) >= 32 for c in value)


class WechatGateway:
    def __init__(self, config=None, transport=None, clock=None):
        if config is None:
            from wechat_integration.credentials import CredentialsUnavailable, effective_app_id
            config = load_wechat_config()
            try:
                app_id = effective_app_id()
            except CredentialsUnavailable:
                raise WechatGatewayError('小程序凭据配置暂不可用。', 'WECHAT_NOT_CONFIGURED', 503) from None
            if not app_id or config.app_id != app_id:
                raise WechatGatewayError('支付部署绑定与当前小程序 AppID 不一致。', 'WECHAT_APP_ID_MISMATCH', 503)
        self.config = validate_config(config)
        self.transport = transport or _transport
        self.clock = clock or time.time
        self._certificates = {}
        try:
            self._private_key = serialization.load_pem_private_key(self.config.private_key_pem, password=None)
            self._platform_keys = dict((serial, self._public_key(serial, pem)) for serial, pem in self.config.platform_keys)
            if not isinstance(self._private_key, rsa.RSAPrivateKey) or self._private_key.key_size < 2048:
                raise ValueError("invalid key")
        except (ValueError, TypeError):
            raise WechatGatewayError("微信支付密钥配置不正确。", "WECHAT_NOT_CONFIGURED", 503) from None

    def _public_key(self, serial, pem):
        if b"BEGIN CERTIFICATE" in pem:
            certificate = x509.load_pem_x509_certificate(pem)
            if format(certificate.serial_number, "X") != serial.upper():
                raise ValueError("certificate serial mismatch")
            self._certificates[serial] = certificate
            key = certificate.public_key()
        else:
            key = serialization.load_pem_public_key(pem)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError("invalid key")
        return key

    def _sign(self, message):
        return base64.b64encode(self._private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")

    def verify_signed_body(self, headers, body):
        if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
            raise WechatGatewayError("微信支付报文超出限制。", "WECHAT_SIGNATURE_INVALID", 400)
        values = {key.lower(): value for key, value in headers.items()}
        stamp = values.get("wechatpay-timestamp", "")
        nonce = values.get("wechatpay-nonce", "")
        serial = values.get("wechatpay-serial", "")
        signature = values.get("wechatpay-signature", "")
        signature_type = values.get("wechatpay-signature-type", "WECHATPAY2-SHA256-RSA2048")
        certificate = self._certificates.get(serial)
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        if (not isinstance(stamp, str) or not re.fullmatch(r"[0-9]{1,12}", stamp) or
                not _text(nonce, 128) or serial not in self._platform_keys or
                not isinstance(signature, str) or len(signature) > 1024 or
                signature_type != "WECHATPAY2-SHA256-RSA2048" or
                (certificate and not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc) or
                abs(self.clock() - int(stamp)) > self.config.replay_window_seconds):
            raise WechatGatewayError("微信支付签名或时间戳无效。", "WECHAT_SIGNATURE_INVALID", 401)
        try:
            signed = stamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"
            self._platform_keys[serial].verify(base64.b64decode(signature, validate=True), signed,
                                              padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, ValueError, TypeError, binascii.Error):
            raise WechatGatewayError("微信支付签名无效。", "WECHAT_SIGNATURE_INVALID", 401) from None

    def _request(self, method, path, payload=None, expected=200):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        stamp, nonce = str(int(self.clock())), secrets.token_hex(16)
        signature = self._sign(f"{method}\n{path}\n{stamp}\n{nonce}\n".encode() + body + b"\n")
        authorization = (f'WECHATPAY2-SHA256-RSA2048 mchid="{self.config.merchant_id}",'
                         f'nonce_str="{nonce}",timestamp="{stamp}",'
                         f'serial_no="{self.config.merchant_serial}",signature="{signature}"')
        headers = {"Authorization": authorization, "Accept": "application/json", "Content-Type": "application/json",
                   "User-Agent": "product-mall-payment/1.0"}
        public_ids = [key for key in self._platform_keys if key.startswith("PUB_KEY_ID_")]
        if public_ids:
            headers["Wechatpay-Serial"] = public_ids[0]
        response = self.transport(method, path, headers, body, self.config.timeout_seconds)
        self.verify_signed_body(response.headers, response.body)
        if response.status != expected:
            code = _object(response.body).get("code", "") if response.body else ""
            safe_codes = {"ORDERPAID", "ORDER_CLOSED", "ORDERNOTEXIST", "OUT_TRADE_NO_USED", "SYSTEM_ERROR", "PARAM_ERROR"}
            raise WechatGatewayError("微信支付未完成此操作，请查询订单状态。",
                                     "WECHAT_" + code if code in safe_codes else "WECHAT_PROVIDER_ERROR", 502)
        return _object(response.body) if response.body else {}

    @staticmethod
    def _order_no(order_no):
        if not isinstance(order_no, str) or not re.fullmatch(r"[A-Za-z0-9_*-]{6,32}", order_no):
            raise WechatGatewayError("商户订单号不正确。")
        return order_no

    def prepay(self, order_no, description, amount_fen, openid, expires_at):
        self._order_no(order_no)
        if (not _text(description, 127) or len(description.encode()) > 127 or
                type(amount_fen) is not int or not 1 <= amount_fen <= 9_223_372_036_854_775_807 or
                not _text(openid, 128) or not isinstance(expires_at, datetime) or
                expires_at.utcoffset() is None or expires_at.timestamp() <= self.clock()):
            raise WechatGatewayError("预支付参数不正确。")
        payload = {"appid": self.config.app_id, "mchid": self.config.merchant_id,
                   "description": description, "out_trade_no": order_no, "time_expire": expires_at.isoformat(),
                   "notify_url": self.config.notify_url, "amount": {"total": amount_fen, "currency": "CNY"},
                   "payer": {"openid": openid}}
        result = self._request("POST", "/v3/pay/transactions/jsapi", payload)
        prepay_id = result.get("prepay_id")
        if not _text(prepay_id, 128) or len("prepay_id=" + prepay_id) > 128:
            raise WechatGatewayError("微信支付未返回有效预支付标识。", "WECHAT_RESPONSE_INVALID", 502)
        return {"prepayId": prepay_id, "paymentParameters": self.payment_parameters(prepay_id)}

    def payment_parameters(self, prepay_id):
        if not _text(prepay_id, 118):
            raise WechatGatewayError("预支付标识不正确。")
        stamp, nonce, package = str(int(self.clock())), secrets.token_hex(16), "prepay_id=" + prepay_id
        signature = self._sign(f"{self.config.app_id}\n{stamp}\n{nonce}\n{package}\n".encode())
        return {"timeStamp": stamp, "nonceStr": nonce, "package": package, "signType": "RSA", "paySign": signature}

    def query(self, order_no):
        self._order_no(order_no)
        return self._request("GET", f"/v3/pay/transactions/out-trade-no/{order_no}?" + urlencode({"mchid": self.config.merchant_id}))

    def close(self, order_no):
        self._order_no(order_no)
        self._request("POST", f"/v3/pay/transactions/out-trade-no/{order_no}/close", {"mchid": self.config.merchant_id}, 204)

    def verify_notification(self, headers, raw_body):
        self.verify_signed_body(headers, raw_body)
        notification = _object(raw_body)
        resource, event_id = notification.get("resource"), notification.get("id")
        if (not _text(event_id, 128) or notification.get("event_type") != "TRANSACTION.SUCCESS" or
                notification.get("resource_type") != "encrypt-resource" or not isinstance(resource, dict) or
                resource.get("algorithm") != "AEAD_AES_256_GCM" or resource.get("original_type") != "transaction"):
            raise WechatGatewayError("微信支付通知类型不正确。", "WECHAT_NOTIFICATION_INVALID")
        try:
            nonce, associated = resource["nonce"], resource.get("associated_data", "")
            if not isinstance(nonce, str) or len(nonce.encode()) != 12 or not isinstance(associated, str):
                raise ValueError("invalid nonce")
            plaintext = AESGCM(self.config.api_v3_key).decrypt(nonce.encode(),
                base64.b64decode(resource["ciphertext"], validate=True), associated.encode())
        except (KeyError, ValueError, TypeError, InvalidTag, binascii.Error):
            raise WechatGatewayError("微信支付通知解密失败。", "WECHAT_NOTIFICATION_INVALID") from None
        return event_id, _object(plaintext)
