"""One non-overlapping, bounded local maintenance pass for deployment scheduling.

No real payment, refund, logistics, or subscription-send adapter is called.
Export generation and retention have their own worker.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from catalog.media import purge_expired_orphans
from checkout.models import CheckoutQuote
from customers.models import MemberSession, WechatCodeUse, WechatLoginAttempt
from fulfillment.service import auto_confirm_receipts
from notifications.service import recover_expired_claims
from orders.models import Order
from orders.service import close_expired_orders


LOGGER = logging.getLogger(__name__)
MAINTENANCE_LOCK = 4995947934981377605
LOCK_CONNECTION = ContextVar("maintenance_lock_connection", default=None)


@contextmanager
def maintenance_lock():
    """A session-level PostgreSQL lock excludes duplicate scheduler containers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [MAINTENANCE_LOCK])
        acquired = cursor.fetchone()[0]
        token = LOCK_CONNECTION.set(connection.connection if acquired else None)
        try:
            yield acquired
        finally:
            try:
                if acquired and connection.connection is LOCK_CONNECTION.get() and connection.is_usable():
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [MAINTENANCE_LOCK])
            finally:
                LOCK_CONNECTION.reset(token)


def _ensure_lock_session():
    expected = LOCK_CONNECTION.get()
    if expected is not None and (connection.connection is not expected or not connection.is_usable()):
        raise RuntimeError("maintenance lock session was lost")


def _delete_batch(queryset, limit):
    ids = list(queryset.values_list("pk", flat=True)[:limit])
    if ids:
        queryset.model.objects.filter(pk__in=ids).delete()
    return len(ids)


def purge_local_records(limit):
    """Small, idempotent batches; retained quotes exclude all order references."""
    now = timezone.now()
    quotes = CheckoutQuote.objects.filter(expires_at__lt=now).exclude(
        id__in=Order.objects.values("quote_id")).order_by("expires_at", "id")
    quote_count = _delete_batch(quotes, limit)
    targets = (
        ("login_attempts", WechatLoginAttempt, "created_at", now - timedelta(days=1)),
        ("code_uses", WechatCodeUse, "created_at", now - timedelta(days=30)),
        ("member_sessions", MemberSession, "expires_at", now - timedelta(days=30)),
    )
    counts = {
        label: _delete_batch(model.objects.filter(**{f"{field}__lt": cutoff})
                             .order_by(field, "pk"), limit)
        for label, model, field, cutoff in targets
    }
    return {"quotes": quote_count, **counts}


def _management_job(name, limit):
    call_command(name, limit=limit, stdout=StringIO())
    return "RUN"


def _run_job(name, operation):
    try:
        result = operation()
        return result if isinstance(result, dict) else {name: result}
    except Exception as exc:
        LOGGER.exception("maintenance job failed: %s", name)
        return {name: f"FAILED:{type(exc).__name__}"}


def run_jobs(limit):
    """Attempt independent safe jobs even if one fails; expose every failure."""
    jobs = (
        ("closed", lambda: close_expired_orders(limit)),
        ("auto_confirmed", lambda: auto_confirm_receipts(limit)),
        ("message_recovered", lambda: recover_expired_claims(limit=limit)),
        ("points", lambda: _management_job("expire_points", limit)),
        ("local_records", lambda: purge_local_records(limit)),
        ("read_quota", lambda: _management_job("purge_admin_read_quota", limit)),
        ("media_orphans", lambda: purge_expired_orphans(limit=limit)),
    )
    outcomes = {}
    for name, operation in jobs:
        _ensure_lock_session()
        outcomes = {**outcomes, **_run_job(name, operation)}
        _ensure_lock_session()
    return outcomes


class Command(BaseCommand):
    help = "Run one bounded local-only maintenance tick; schedule once per minute."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if type(limit) is not int or not 1 <= limit <= 500:
            raise CommandError("limit must be between 1 and 500")
        with maintenance_lock() as acquired:
            if not acquired:
                raise CommandError("maintenance tick already running")
            outcomes = run_jobs(limit)
        self.stdout.write(" ".join(f"{key}={value}" for key, value in outcomes.items()))
        if any(str(value).startswith("FAILED:") for value in outcomes.values()):
            raise CommandError("maintenance jobs failed; inspect server logs and retry")
