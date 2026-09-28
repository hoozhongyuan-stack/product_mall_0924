"""Freeze one bounded E4.0 filter set at export-request time."""

import hashlib
import json
from types import SimpleNamespace

from django.http import QueryDict

from accounts.audit_read import _filters as audit_filters
from accounts.read_window import read_window


AUDIT_KEYS = frozenset({"from", "to", "actorId", "actionCode", "objectType", "objectId", "result"})
BUSINESS_KEYS = frozenset({"from", "to"})


def normalize_filters(kind, raw):
    allowed = AUDIT_KEYS if kind == "AUDIT" else BUSINESS_KEYS if kind == "BUSINESS" else None
    if allowed is None or not isinstance(raw, dict) or set(raw) - allowed or any(
            not isinstance(value, str) or len(value) > 100 for value in raw.values()):
        raise ValueError("导出筛选参数不正确。")
    if kind == "AUDIT":
        query = QueryDict(mutable=True)
        for key, value in raw.items():
            query[key] = value
        first, last, _, _, actor, action, object_type, object_id, result, _ = audit_filters(
            SimpleNamespace(GET=query))
        normalized = {"from": first.isoformat(), "to": last.isoformat()}
        for key, value in (("actorId", str(actor) if actor else ""), ("actionCode", action),
                           ("objectType", object_type), ("objectId", object_id), ("result", result)):
            if value:
                normalized[key] = value
        return normalized
    first, last, _, _ = read_window(raw)
    return {"from": first.isoformat(), "to": last.isoformat()}


def filter_digest(kind, normalized):
    payload = json.dumps({"kind": kind, "filters": normalized}, ensure_ascii=True,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()
