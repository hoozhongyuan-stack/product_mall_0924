from django.core.management.base import BaseCommand

from orders.service import close_expired_orders


class Command(BaseCommand):
    help = "Close expired pending orders and release their inventory reservations."

    def handle(self, *args, **options):
        total = 0
        while count := close_expired_orders():
            total += count
        self.stdout.write(f"orders closed: {total}")
