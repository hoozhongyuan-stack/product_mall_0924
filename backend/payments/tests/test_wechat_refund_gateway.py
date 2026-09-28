"""Ephemeral signing material; tests never contact WeChat."""
import base64
import json
from unittest.mock import Mock
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import SimpleTestCase
from payments.wechat_config import WechatConfig, WechatGatewayError
from payments.wechat_gateway import GatewayResponse
from payments.wechat_refund_gateway import WechatRefundGateway


class WechatRefundGatewayTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.provider = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.config = WechatConfig(app_id="wxRefundTest", merchant_id="1900000001", merchant_serial="ABCD",
            notify_url="https://shop.example.test/payment", api_v3_key=b"r" * 32,
            private_key_pem=cls.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()), platform_keys=(("PUB_KEY_ID_123", cls.provider.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)),))

    def headers(self, body):
        signed = b"1800000000\nnonce\n" + body + b"\n"
        return {"Wechatpay-Timestamp": "1800000000", "Wechatpay-Nonce": "nonce", "Wechatpay-Serial": "PUB_KEY_ID_123",
            "Wechatpay-Signature": base64.b64encode(self.provider.sign(signed, padding.PKCS1v15(), hashes.SHA256())).decode()}

    def gateway(self, status=200, payload=None):
        body = json.dumps(payload or {"status": "PROCESSING"}).encode()
        transport = Mock(return_value=GatewayResponse(status, self.headers(body), body))
        return WechatRefundGateway(self.config, transport, clock=lambda: 1800000000,
            refund_notify_url="https://shop.example.test/refund"), transport

    def test_dispatch_signed_exact_server_facts_and_stable_refund_number(self):
        g, transport = self.gateway()
        result = g.refund("R" + "A" * 32, "WX-ORIGINAL", 1000, 2000)
        self.assertEqual(result["status"], "PROCESSING")
        method, path, headers, body, _ = transport.call_args.args
        self.assertEqual((method, path), ("POST", "/v3/refund/domestic/refunds"))
        self.assertEqual(json.loads(body), {"transaction_id": "WX-ORIGINAL", "out_refund_no": "R" + "A" * 32,
            "notify_url": "https://shop.example.test/refund", "amount": {"refund": 1000, "total": 2000, "currency": "CNY"}})
        fields = dict(p.split("=", 1) for p in headers["Authorization"].split(" ", 1)[1].split(","))
        fields = {k: v.strip('"') for k, v in fields.items()}
        self.key.public_key().verify(base64.b64decode(fields["signature"]),
            f'{method}\n{path}\n{fields["timestamp"]}\n{fields["nonce_str"]}\n'.encode() + body + b"\n",
            padding.PKCS1v15(), hashes.SHA256())

    def test_query_path_and_signed_not_found_are_safe(self):
        g, transport = self.gateway(404, {"code": "RESOURCE_NOT_EXISTS", "message": "private"})
        with self.assertRaises(WechatGatewayError) as caught:
            g.query_refund("R" + "A" * 32)
        self.assertEqual(caught.exception.code, "WECHAT_REFUND_NOT_FOUND")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(transport.call_args.args[:2], ("GET", "/v3/refund/domestic/refunds/R" + "A" * 32))

    def test_inputs_and_unsigned_errors_fail_without_proof(self):
        g, transport = self.gateway()
        for args in [("../unsafe", "WX-ORIGINAL", 1, 2), ("R123456", "", 1, 2),
                     ("R123456", "WX-ORIGINAL", True, 2), ("R123456", "WX-ORIGINAL", 3, 2)]:
            with self.assertRaises(WechatGatewayError):
                g.refund(*args)
        transport.assert_not_called()
        g.transport = Mock(return_value=GatewayResponse(404, {}, b'{"code":"RESOURCE_NOT_EXISTS"}'))
        with self.assertRaises(WechatGatewayError) as caught:
            g.query_refund("R123456")
        self.assertEqual(caught.exception.code, "WECHAT_SIGNATURE_INVALID")
        with self.assertRaises(WechatGatewayError):
            WechatRefundGateway(self.config, refund_notify_url="http://unsafe.test/refund")

    def notification(self, status="SUCCESS", event_id="refund-event"):
        payload = {"refund_status": status, "mchid": self.config.merchant_id}
        cipher = AESGCM(self.config.api_v3_key).encrypt(b"123456789012", json.dumps(payload).encode(), b"refund")
        value = {"id": event_id, "event_type": "REFUND." + status, "resource_type": "encrypt-resource",
            "resource": {"algorithm": "AEAD_AES_256_GCM", "original_type": "refund", "nonce": "123456789012",
                "associated_data": "refund", "ciphertext": base64.b64encode(cipher).decode()}}
        return value

    def test_refund_notifications_verified_decrypted_and_status_bound(self):
        g, _ = self.gateway()
        for status in ["SUCCESS", "CLOSED", "ABNORMAL"]:
            raw = json.dumps(self.notification(status)).encode()
            event, data = g.verify_refund_notification(self.headers(raw), raw)
            self.assertEqual((event, data["refund_status"]), ("refund-event", status))
        for mutation in [{"event_type": "TRANSACTION.SUCCESS"}, {"id": ""}]:
            raw = json.dumps({**self.notification(), **mutation}).encode()
            with self.assertRaises(WechatGatewayError):
                g.verify_refund_notification(self.headers(raw), raw)
        value = self.notification()
        for mutation in [{"nonce": "bad"}, {"algorithm": "AES"}, {"ciphertext": "bad"}, {"original_type": "transaction"}]:
            raw = json.dumps({**value, "resource": {**value["resource"], **mutation}}).encode()
            with self.assertRaises(WechatGatewayError):
                g.verify_refund_notification(self.headers(raw), raw)
