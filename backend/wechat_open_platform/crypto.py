"""Purpose-bound encryption and authenticated WeChat XML callbacks."""
import base64
import hashlib
import hmac
import json
import struct
import xml.etree.ElementTree as ET

from cryptography.exceptions import InvalidKey
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from wechat_integration.credentials import CredentialsUnavailable, cipher


class InvalidCallback(ValueError):
    pass


def seal(purpose, value):
    return cipher().encrypt(json.dumps({'purpose': purpose, 'value': value}).encode()).decode('ascii')


def unseal(purpose, encrypted):
    try:
        payload = json.loads(cipher().decrypt(encrypted.encode()))
        if not isinstance(payload, dict) or payload.get('purpose') != purpose:
            raise ValueError()
        return payload['value']
    except (InvalidToken, KeyError, TypeError, ValueError, UnicodeError):
        raise CredentialsUnavailable() from None


def _xml_fields(raw):
    if (not isinstance(raw, bytes) or len(raw) > 131072
            or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper()):
        raise InvalidCallback('消息格式不正确。')
    try:
        element = ET.fromstring(raw)
    except ET.ParseError:
        raise InvalidCallback('消息格式不正确。') from None
    if element.tag != 'xml':
        raise InvalidCallback('消息格式不正确。')
    return {child.tag: child.text or '' for child in element}


def decode_callback(raw, *, signature, timestamp, nonce, token, encoding_key, component_app_id):
    """Verify msg_signature (never signature), decrypt, then bind appid."""
    outer = _xml_fields(raw)
    encrypted = outer.get('Encrypt', '')
    if (not encrypted or not isinstance(signature, str) or len(signature) != 40
            or not isinstance(timestamp, str) or not timestamp.isdigit()
            or not isinstance(nonce, str) or len(nonce) > 128):
        raise InvalidCallback('消息签名不正确。')
    digest = hashlib.sha1(''.join(sorted((token, timestamp, nonce, encrypted))).encode()).hexdigest()
    if not hmac.compare_digest(digest, signature):
        raise InvalidCallback('消息签名不正确。')
    try:
        key = base64.b64decode(encoding_key + '=', validate=True)
        if len(key) != 32:
            raise ValueError()
        ciphertext = base64.b64decode(encrypted, validate=True)
        if not ciphertext or len(ciphertext) % 16:
            raise ValueError()
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        size = padded[-1]
        if not (1 <= size <= 32) or padded[-size:] != bytes([size]) * size:
            raise ValueError()
        clear = padded[:-size]
        message_size = struct.unpack('!I', clear[16:20])[0]
        if message_size > 65536 or 20 + message_size > len(clear):
            raise ValueError()
        message = clear[20:20 + message_size]
        app_id = clear[20 + message_size:].decode()
        if not hmac.compare_digest(app_id, component_app_id):
            raise ValueError()
        return _xml_fields(message)
    except (ValueError, TypeError, IndexError, UnicodeError, struct.error, InvalidKey):
        raise InvalidCallback('消息解密或平台标识校验失败。') from None
