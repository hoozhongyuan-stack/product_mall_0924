"""APIv3 domestic refund transport; reuses payment signing and key verification.

Request acceptance is not final money evidence. Signed query or encrypted refund
notification is the only source of final refund success in the service layer.
"""
import base64
import binascii
import json
import re
import secrets
from urllib.parse import urlsplit
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from .wechat_config import WechatGatewayError
from .wechat_gateway import WechatGateway, _object, _text


class WechatRefundGateway(WechatGateway):
    def __init__(self, config=None, transport=None, clock=None, *, refund_notify_url=None):
        super().__init__(config, transport, clock)
        url = refund_notify_url if refund_notify_url is not None else getattr(settings, "WECHAT_REFUND_NOTIFY_URL", "")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
        except ValueError:
            raise WechatGatewayError("微信退款通知地址配置不正确。", "WECHAT_REFUND_NOT_CONFIGURED", 503) from None
        if (not parsed or len(url) > 256 or parsed.scheme != "https" or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path or
                any(ord(c) < 32 for c in url)):
            raise WechatGatewayError("微信退款通知地址尚未完成配置。", "WECHAT_REFUND_NOT_CONFIGURED", 503)
        self.refund_notify_url = url

    @staticmethod
    def _refund_no(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_*-]{6,64}", value):
            raise WechatGatewayError("商户退款单号不正确。", "WECHAT_REFUND_INVALID")
        return value

    def _refund_request(self, method, path, payload=None):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        stamp, nonce = str(int(self.clock())), secrets.token_hex(16)
        signature = self._sign(f"{method}\n{path}\n{stamp}\n{nonce}\n".encode() + body + b"\n")
        headers = {"Authorization": (f'WECHATPAY2-SHA256-RSA2048 mchid="{self.config.merchant_id}",'
            f'nonce_str="{nonce}",timestamp="{stamp}",serial_no="{self.config.merchant_serial}",signature="{signature}"'),
            "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "product-mall-refund/1.0"}
        public = [key for key in self._platform_keys if key.startswith("PUB_KEY_ID_")]
        if public:
            headers["Wechatpay-Serial"] = public[0]
        response = self.transport(method, path, headers, body, self.config.timeout_seconds)
        self.verify_signed_body(response.headers, response.body)
        data = _object(response.body)
        if response.status != 200:
            code = data.get("code")
            if method == "GET" and response.status == 404 and code == "RESOURCE_NOT_EXISTS":
                safe = "WECHAT_REFUND_NOT_FOUND"
            elif method == "POST" and response.status in (400, 403) and code in {"NOT_ENOUGH", "PARAM_ERROR", "INVALID_REQUEST"}:
                safe = "WECHAT_REFUND_REJECTED"
            else:
                safe = "WECHAT_REFUND_PROVIDER_ERROR"
            raise WechatGatewayError("微信退款操作未完成，请先查询退款状态。", safe, 502)
        return data

    def refund(self, refund_no, transaction_id, amount_fen, total_fen):
        self._refund_no(refund_no)
        if (not _text(transaction_id, 128) or type(amount_fen) is not int or type(total_fen) is not int or
                not 0 < amount_fen <= total_fen <= 2**63 - 1):
            raise WechatGatewayError("微信退款资金参数不正确。", "WECHAT_REFUND_INVALID")
        return self._refund_request("POST", "/v3/refund/domestic/refunds", {
            "transaction_id": transaction_id, "out_refund_no": refund_no, "notify_url": self.refund_notify_url,
            "amount": {"refund": amount_fen, "total": total_fen, "currency": "CNY"}})

    def query_refund(self, refund_no):
        return self._refund_request("GET", "/v3/refund/domestic/refunds/" + self._refund_no(refund_no))

    def verify_refund_notification(self, headers, raw):
        self.verify_signed_body(headers, raw)
        envelope = _object(raw)
        resource, event = envelope.get("resource"), envelope.get("id")
        states = {"REFUND.SUCCESS": "SUCCESS", "REFUND.CLOSED": "CLOSED", "REFUND.ABNORMAL": "ABNORMAL"}
        if (not _text(event, 128) or envelope.get("event_type") not in states or
                envelope.get("resource_type") != "encrypt-resource" or not isinstance(resource, dict) or
                resource.get("algorithm") != "AEAD_AES_256_GCM" or resource.get("original_type") != "refund"):
            raise WechatGatewayError("微信退款通知类型不正确。", "WECHAT_REFUND_NOTIFICATION_INVALID")
        try:
            nonce, associated = resource["nonce"], resource.get("associated_data", "")
            if not isinstance(nonce, str) or len(nonce.encode()) != 12 or not isinstance(associated, str):
                raise ValueError()
            plain = AESGCM(self.config.api_v3_key).decrypt(nonce.encode(),
                base64.b64decode(resource["ciphertext"], validate=True), associated.encode())
        except (KeyError, ValueError, TypeError, InvalidTag, binascii.Error):
            raise WechatGatewayError("微信退款通知解密失败。", "WECHAT_REFUND_NOTIFICATION_INVALID") from None
        data = _object(plain)
        if data.get("refund_status") != states[envelope["event_type"]]:
            raise WechatGatewayError("微信退款通知状态不一致。", "WECHAT_REFUND_NOTIFICATION_INVALID")
        return event, data
