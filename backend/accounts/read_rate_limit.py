"""Database-backed cross-worker quota for private read endpoints."""

from datetime import timedelta
from math import ceil

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import AdminReadQuota
from .security import error


LIMITS = {"audit": 60, "business": 30, "wechat.integration": 30}


def read_rate_limit(request, actor, scope):
    limit = LIMITS[scope]
    now = timezone.now()
    window_start = now.replace(second=0, microsecond=0)
    with transaction.atomic():
        row, _ = AdminReadQuota.objects.select_for_update().get_or_create(
            actor=actor, scope=scope, window_start=window_start)
        if row.count >= limit:
            limited = error(request, 429, "RATE_LIMITED", "查询过于频繁，请稍后再试。")
            limited["Retry-After"] = str(max(1, ceil((window_start + timedelta(minutes=1) - now).total_seconds())))
            return limited
        AdminReadQuota.objects.filter(pk=row.pk).update(count=F("count") + 1)
    return None
