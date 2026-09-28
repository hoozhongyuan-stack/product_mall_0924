"""Bounded retention for per-minute private-read quota buckets."""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import AdminReadQuota


def purge_read_quota(limit=100):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    cutoff = timezone.now() - timedelta(days=1)
    ids = list(AdminReadQuota.objects.filter(window_start__lt=cutoff)
               .order_by("window_start", "id").values_list("id", flat=True)[:limit])
    if ids:
        AdminReadQuota.objects.filter(pk__in=ids).delete()
    return len(ids)


class Command(BaseCommand):
    help = "Delete at most N expired admin read-rate buckets; retain the last day."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        try:
            count = purge_read_quota(options["limit"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"read_quota_deleted={count}")
