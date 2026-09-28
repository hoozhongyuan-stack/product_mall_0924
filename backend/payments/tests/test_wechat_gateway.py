"""Local ephemeral-key tests; no merchant secrets and no provider traffic."""

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from django.test import SimpleTestCase, override_settings
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from payments.wechat_config import WechatConfig, WechatGatewayError, load_wechat_config
from payments.wechat_gateway import GatewayResponse, WechatGateway, _NoRedirect, _transport


class WechatGatewayTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.provider_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = cls.merchant_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        cls.public_pem = cls.provider_key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.config = WechatConfig(app_id="wxTestApp123", merchant_id="1900000001", merchant_serial="AABB12",
            notify_url="https://shop.example.test/api/v1/payments/wechat/notify", api_v3_key=b"a" * 32,
            private_key_pem=cls.private_pem, platform_keys=(("PUB_KEY_ID_123", cls.public_pem),))
        cls.now = 1800000000

    def setUp(self):
        self.gateway = WechatGateway(self.config, clock=lambda: self.now)

    def headers(self, body, stamp=None, serial="PUB_KEY_ID_123"):
        stamp = str(self.now if stamp is None else stamp)
        signed = stamp.encode() + b"\nnonce\n" + body + b"\n"
        signature = self.provider_key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        return {"Wechatpay-Timestamp": stamp, "Wechatpay-Nonce": "nonce", "Wechatpay-Serial": serial,
                "Wechatpay-Signature": base64.b64encode(signature).decode()}

    def test_verify_rejects_tampering_missing_signature_unknown_key_and_stale_timestamp(self):
        body = b'{"trusted":true}'
        self.gateway.verify_signed_body(self.headers(body), body)
        variants = [({}, body), (self.headers(body), body + b" "),
                    (self.headers(body, serial="PUB_KEY_ID_999"), body),
                    (self.headers(body, stamp=self.now - 301), body),
                    (self.headers(body, stamp=self.now + 301), body)]
        for headers, raw in variants:
            with self.subTest(headers=list(headers)), self.assertRaises(WechatGatewayError):
                self.gateway.verify_signed_body(headers, raw)

    def test_signature_probe_and_wrong_signature_algorithm_rejected(self):
        headers = self.headers(b"{}")
        for field, value in [("Wechatpay-Signature", "WECHATPAY/SIGNTEST/invalid"),
                             ("Wechatpay-Signature-Type", "MD5")]:
            with self.subTest(field=field), self.assertRaises(WechatGatewayError):
                self.gateway.verify_signed_body({**headers, field: value}, b"{}")

    def notification(self, payload=None):
        plaintext = json.dumps(payload or {"appid": self.config.app_id, "mchid": self.config.merchant_id,
            "out_trade_no": "a" * 32, "trade_state": "SUCCESS", "amount": {"total": 2000, "currency": "CNY"}}).encode()
        encrypted = AESGCM(self.config.api_v3_key).encrypt(b"123456789012", plaintext, b"transaction")
        return {"id": "event-1", "event_type": "TRANSACTION.SUCCESS", "resource_type": "encrypt-resource",
                "resource": {"algorithm": "AEAD_AES_256_GCM", "original_type": "transaction",
                    "nonce": "123456789012", "associated_data": "transaction",
                    "ciphertext": base64.b64encode(encrypted).decode()}}

    def test_signed_notification_decrypted_after_verification(self):
        raw = json.dumps(self.notification()).encode()
        event_id, payload = self.gateway.verify_notification(self.headers(raw), raw)
        self.assertEqual(event_id, "event-1")
        self.assertEqual(payload["amount"]["total"], 2000)
        self.assertEqual(payload["mchid"], self.config.merchant_id)

    def test_invalid_ciphertext_algorithm_event_and_nonce_fail_closed(self):
        original = self.notification()
        variants = [{**original, "event_type": "TRANSACTION.CLOSED"},
                    {**original, "resource": {**original["resource"], "algorithm": "AES"}},
                    {**original, "resource": {**original["resource"], "ciphertext": "AAAA"}},
                    {**original, "resource": {**original["resource"], "nonce": "short"}}]
        for notification in variants:
            raw = json.dumps(notification).encode()
            with self.subTest(notification=notification["event_type"]), self.assertRaises(WechatGatewayError):
                self.gateway.verify_notification(self.headers(raw), raw)

    def test_prepay_request_signature_body_and_client_signature(self):
        calls = []
        def transport(method, path, headers, body, timeout):
            calls.append((method, path, headers, body, timeout))
            response = b'{"prepay_id":"wx_local_only"}'
            return GatewayResponse(200, self.headers(response), response)
        gateway = WechatGateway(self.config, transport, clock=lambda: self.now)
        result = gateway.prepay("a" * 32, "测试订单", 2000, "trusted-openid",
                                datetime.fromtimestamp(self.now + 1800, timezone.utc))
        method, path, headers, body, timeout = calls[0]
        self.assertEqual((method, path, timeout), ("POST", "/v3/pay/transactions/jsapi", 8))
        payload = json.loads(body)
        self.assertEqual(payload["amount"], {"total": 2000, "currency": "CNY"})
        self.assertEqual(payload["payer"], {"openid": "trusted-openid"})
        fields = dict(part.split("=", 1) for part in headers["Authorization"].split(" ", 1)[1].split(","))
        fields = {key: value.strip('"') for key, value in fields.items()}
        message = f'{method}\n{path}\n{fields["timestamp"]}\n{fields["nonce_str"]}\n'.encode() + body + b"\n"
        self.merchant_key.public_key().verify(base64.b64decode(fields["signature"]), message,
                                              padding.PKCS1v15(), hashes.SHA256())
        params = result["paymentParameters"]
        message = f'{self.config.app_id}\n{params["timeStamp"]}\n{params["nonceStr"]}\n{params["package"]}\n'.encode()
        self.merchant_key.public_key().verify(base64.b64decode(params["paySign"]), message,
                                              padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual(params["signType"], "RSA")

    def test_query_and_signed_empty_close_response(self):
        calls = []
        def transport(method, path, headers, body, timeout):
            calls.append((method, path, body))
            raw = b'{"trade_state":"NOTPAY"}' if method == "GET" else b""
            return GatewayResponse(200 if method == "GET" else 204, self.headers(raw), raw)
        gateway = WechatGateway(self.config, transport, clock=lambda: self.now)
        self.assertEqual(gateway.query("a" * 32)["trade_state"], "NOTPAY")
        self.assertIsNone(gateway.close("a" * 32))
        self.assertIn("?mchid=1900000001", calls[0][1])
        self.assertEqual(json.loads(calls[1][2]), {"mchid": "1900000001"})

    def test_unsigned_success_and_signed_errors_not_used_as_success(self):
        for response in [GatewayResponse(200, {}, b'{"prepay_id":"fake"}'),
                         GatewayResponse(400, self.headers(b'{"code":"ORDERPAID","message":"private data"}'),
                                         b'{"code":"ORDERPAID","message":"private data"}')]:
            gateway = WechatGateway(self.config, lambda *args: response, clock=lambda: self.now)
            with self.subTest(status=response.status), self.assertRaises(WechatGatewayError) as caught:
                gateway.query("a" * 32)
            self.assertNotIn("private data", str(caught.exception))

    def test_input_limits_and_invalid_config_do_not_send_requests(self):
        with patch("payments.wechat_gateway._transport") as transport:
            for order_no in ["short", "a" * 33, "abc123/../../", "abc123?mchid=999"]:
                with self.subTest(order_no=order_no), self.assertRaises(WechatGatewayError):
                    self.gateway.query(order_no)
            for amount in [True, 0, -1, 9_223_372_036_854_775_808]:
                with self.subTest(amount=amount), self.assertRaises(WechatGatewayError):
                    self.gateway.prepay("a" * 32, "订单", amount, "openid",
                        datetime.fromtimestamp(self.now + 60, timezone.utc))
            transport.assert_not_called()
        for config in [replace(self.config, api_v3_key=b"short"),
                       replace(self.config, notify_url="http://example.test/notify"),
                       replace(self.config, timeout_seconds=100),
                       replace(self.config, private_key_pem=b"invalid")]:
            with self.subTest(config=config.app_id), self.assertRaises(WechatGatewayError):
                WechatGateway(config)

    @override_settings(WECHAT_PAY={})
    def test_missing_deployment_config_has_sanitized_failure(self):
        with self.assertRaises(WechatGatewayError) as caught:
            load_wechat_config()
        self.assertEqual(caught.exception.code, "WECHAT_NOT_CONFIGURED")

    def test_load_private_files_and_key_rotation(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "key").write_bytes(self.config.api_v3_key)
            (folder / "private").write_bytes(self.private_pem)
            (folder / "public").write_bytes(self.public_pem)
            values = {"APP_ID": self.config.app_id, "MERCHANT_ID": self.config.merchant_id,
                      "MERCHANT_SERIAL": self.config.merchant_serial, "NOTIFY_URL": self.config.notify_url,
                      "API_V3_KEY_FILE": str(folder / "key"), "PRIVATE_KEY_FILE": str(folder / "private"),
                      "PLATFORM_KEYS": {"PUB_KEY_ID_123": str(folder / "public"), "PUB_KEY_ID_124": str(folder / "public")}}
            with override_settings(WECHAT_PAY=values):
                gateway = WechatGateway(load_wechat_config(), clock=lambda: self.now)
                gateway.verify_signed_body(self.headers(b"{}", serial="PUB_KEY_ID_124"), b"{}")

    def test_pinned_certificate_serial_and_validity_checked(self):
        now = datetime.fromtimestamp(self.now, timezone.utc)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local fixture only")])
        def certificate(start, end):
            return (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(self.provider_key.public_key()).serial_number(0xAABB)
                .not_valid_before(start).not_valid_after(end).sign(self.provider_key, hashes.SHA256())
                .public_bytes(serialization.Encoding.PEM))
        current = certificate(now - timedelta(days=1), now + timedelta(days=1))
        config = replace(self.config, platform_keys=(("AABB", current),))
        WechatGateway(config, clock=lambda: self.now).verify_signed_body(self.headers(b"{}", serial="AABB"), b"{}")
        with self.assertRaises(WechatGatewayError):
            WechatGateway(replace(config, platform_keys=(("AABC", current),)), clock=lambda: self.now)
        expired = certificate(now - timedelta(days=2), now - timedelta(days=1))
        with self.assertRaises(WechatGatewayError):
            WechatGateway(replace(config, platform_keys=(("AABB", expired),)), clock=lambda: self.now).verify_signed_body(
                self.headers(b"{}", serial="AABB"), b"{}")

    def test_production_transport_fixed_host_size_timeout_and_redirect_policy(self):
        response = Mock(status=204, headers={})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b""
        opener = Mock()
        opener.open.return_value = response
        with patch("payments.wechat_gateway.build_opener", return_value=opener):
            result = _transport("POST", "/v3/pay/transactions/out-trade-no/abc123/close", {}, b"{}", 8)
            self.assertEqual(result.status, 204)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.mch.weixin.qq.com/v3/pay/transactions/out-trade-no/abc123/close")
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)
            response.read.assert_called_once_with(65537)
            response.read.return_value = b"a" * 65537
            with self.assertRaises(WechatGatewayError):
                _transport("GET", "/v3/test", {}, b"", 8)
            opener.open.side_effect = URLError("local simulated timeout")
            with self.assertRaises(WechatGatewayError) as caught:
                _transport("GET", "/v3/test", {}, b"", 8)
            self.assertEqual(caught.exception.code, "WECHAT_UNAVAILABLE")
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://unsafe.test"))

    def test_malformed_provider_json_and_missing_prepay_id_rejected(self):
        for raw in [b"not-json", b"[]", b"{}", b'"unicode"']:
            gateway = WechatGateway(self.config, lambda *args: GatewayResponse(200, self.headers(raw), raw),
                                    clock=lambda: self.now)
            with self.subTest(raw=raw), self.assertRaises(WechatGatewayError):
                gateway.prepay("a" * 32, "订单", 1, "openid",
                               datetime.fromtimestamp(self.now + 60, timezone.utc))

    def test_malformed_configuration_is_fail_closed(self):
        for config in [replace(self.config, app_id=None), replace(self.config, merchant_serial='abc"inject'),
                       replace(self.config, platform_keys=(("wrong-id", self.public_pem),)),
                       replace(self.config, platform_keys=(("PUB_KEY_ID_123", b"invalid"),)),
                       replace(self.config, notify_url="https://name:password@example.test/notify"),
                       replace(self.config, api_v3_key="a" * 32),
                       replace(self.config, platform_keys=(("PUB_KEY_ID_123", self.public_pem),) * 2)]:
            with self.subTest(appid=config.app_id), self.assertRaises(WechatGatewayError):
                WechatGateway(config)

    def test_positive_bigint_amount_has_no_invented_business_cap(self):
        body=b'{"prepay_id":"test_bigint"}'
        captured=[]
        def transport(method,path,headers,payload,timeout):
            captured.append(json.loads(payload)['amount']['total'])
            return GatewayResponse(200,self.headers(body),body)
        gateway=WechatGateway(self.config,transport=transport,clock=lambda:self.now)
        gateway.prepay('a'*32,'测试订单',100000001,'trusted-openid',datetime.fromtimestamp(self.now+60,timezone.utc))
        self.assertEqual(captured,[100000001])
