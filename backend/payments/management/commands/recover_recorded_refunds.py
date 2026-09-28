from django.core.management.base import BaseCommand, CommandError
from payments.refunds import RefundError, recover_recorded_refunds


class Command(BaseCommand):
    help = "Recover persisted refund evidence locally; never dispatch a new refund."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        try:
            result = recover_recorded_refunds(options["limit"])
        except RefundError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(" ".join(f"{key}={value}" for key, value in result.items()))
        if result["failed"] or result["anomalies"]:
            raise CommandError("Refund recovery has unresolved results.")
