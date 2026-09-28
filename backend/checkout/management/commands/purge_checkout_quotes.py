from django.core.management.base import BaseCommand
from django.utils import timezone

from checkout.models import CheckoutQuote
from orders.models import Order


class Command(BaseCommand):
    help = "Delete expired, unconsumed quote snapshots; schedule daily."

    def handle(self, *args, **options):
        retained = Order.objects.values("quote_id")
        count, _ = CheckoutQuote.objects.filter(expires_at__lt=timezone.now()).exclude(id__in=retained).delete()
        self.stdout.write(f"checkout quotes deleted: {count}")
