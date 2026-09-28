"""Input validation at the inventory API boundary."""

import re
import uuid


class InventoryError(Exception):
    def __init__(self, message, code="VALIDATION_FAILED", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def code_field(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise InventoryError(f"{label}只接受 1—64 位英文字母、数字、- 和 _。")
    return value


def text_field(value, label, maximum):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise InventoryError(f"{label}长度必须在 1—{maximum} 字符之间。")
    return value.strip()


def number_field(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InventoryError(f"{label}必须是 {minimum} 到 {maximum} 之间的整数。")
    return value


def uuid_field(value, label):
    try:
        if not isinstance(value, str):
            raise ValueError
        return uuid.UUID(value)
    except ValueError as exc:
        raise InventoryError(f"{label}格式不正确。") from exc
