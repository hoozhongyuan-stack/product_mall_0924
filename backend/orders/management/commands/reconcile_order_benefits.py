"""Bounded recovery of local benefit projections; never dispatches funds."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from benefits.models import OrderBenefitSnapshot
from orders.models import Order
from orders.benefit_lifecycle import reconcile_benefits_locked


class Command(BaseCommand):
    help = 'Reconcile benefit snapshots for paid orders in bounded, cursor-based batches.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=100)
        parser.add_argument('--after-order-id', default='')

    def handle(self, *args, **options):
        from uuid import UUID
        size = options['batch_size']
        if not 1 <= size <= 500:
            raise CommandError('batch-size must be between 1 and 500')
        rows = Order.objects.filter(status='PAID', id__in=OrderBenefitSnapshot.objects.values('order_id')).order_by('id')
        if options['after_order_id']:
            try:
                rows = rows.filter(id__gt=UUID(options['after_order_id']))
            except ValueError as exc:
                raise CommandError('after-order-id must be UUID') from exc
        ids = list(rows.values_list('id', flat=True)[:size])
        applied = failed = 0
        for order_id in ids:
            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(pk=order_id)
                    reconcile_benefits_locked(order)
                applied += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f'order={order_id} failed={type(exc).__name__}')
        self.stdout.write(f'checked={len(ids)} applied={applied} failed={failed} nextCursor={ids[-1] if ids else ""}')
        if failed:
            raise CommandError('Some benefit projections require recovery')
