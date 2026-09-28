"""Bounded expiry sweep; no payment/fulfillment network calls."""
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from benefits.lifecycle import expire_points
from benefits.models import PointsGrant


class Command(BaseCommand):
    help = 'Expire available points for a bounded batch of members.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 500:
            raise CommandError('limit must be between 1 and 500')
        at = timezone.now()
        member_ids = list(PointsGrant.objects.filter(expires_at__lte=at, available_points__gt=0)
                          .order_by('member_id').values_list('member_id', flat=True).distinct()[:limit])
        expired = sum(expire_points(SimpleNamespace(id=member_id), at=at) for member_id in member_ids)
        self.stdout.write(f'checked={len(member_ids)} expired_lots={expired}')
