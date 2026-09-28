"""Shared, bounded Shanghai-calendar window for private admin read models."""

from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from django.utils import timezone

SHANGHAI = ZoneInfo("Asia/Shanghai")


def read_window(params):
    today = timezone.localtime(timezone.now(), SHANGHAI).date()
    try:
        if any(params.get(key) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", params[key]) for key in ("from", "to")):
            raise ValueError
        first = date.fromisoformat(params.get("from", "")) if params.get("from") else today - timedelta(days=89)
        last = date.fromisoformat(params.get("to", "")) if params.get("to") else today
        if first > last or (last - first).days >= 90:
            raise ValueError
        start = datetime.combine(first, time.min, SHANGHAI)
        end = datetime.combine(last + timedelta(days=1), time.min, SHANGHAI)
    except (ValueError, OverflowError) as exc:
        raise ValueError("日期须为 YYYY-MM-DD，且范围不超过 90 个自然日。") from exc
    return first, last, start, end
