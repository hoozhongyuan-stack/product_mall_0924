"""Derive a strong per-line bearer code without storing its plaintext."""
import base64
import hashlib
import hmac
import re
import secrets
from django.conf import settings


CODE_RE = re.compile(r"^[A-Z2-7]{26}$")


def new_nonce():
    return secrets.token_hex(16)


def voucher_code(line_id, nonce):
    secret = settings.SECRET_KEY.encode("utf-8")
    data = f"C3-REDEEM:{line_id}:{nonce}".encode("ascii")
    raw = hmac.new(secret, data, hashlib.sha256).digest()[:17]
    return base64.b32encode(raw).decode("ascii").rstrip("=")[:26]


def normalized_code(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    code = re.sub(r"[\s-]", "", value).upper()
    return code if CODE_RE.fullmatch(code) else None


def code_digest(code):
    return hashlib.sha256(code.encode("ascii")).hexdigest()
