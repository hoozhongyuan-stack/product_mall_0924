"""Invoke from the deployment scheduler; local development has no scheduler."""
from django.core.management.base import BaseCommand, CommandError
from fulfillment.service import FulfillmentError, auto_confirm_receipts


class Command(BaseCommand):
    help = "确认已超过订单快照期限、仍未由会员确认的实物收货。"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        try:
            count = auto_confirm_receipts(options["limit"])
        except FulfillmentError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"auto_confirmed={count}")
