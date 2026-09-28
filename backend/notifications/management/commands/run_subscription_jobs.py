"""One bounded recovery tick; synthetic processing is a local test opt-in."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from notifications.service import SendResult, process_tasks, recover_expired_claims


class Command(BaseCommand):
    help = "Recover expired message-task leases; optionally simulate local dispatch."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--synthetic", action="store_true")

    def handle(self, *args, **options):
        limit = options["limit"]
        if type(limit) is not int or not 1 <= limit <= 500:
            raise CommandError("limit must be between 1 and 500")
        if options["synthetic"] and not settings.DEBUG:
            raise CommandError("synthetic processing is local-development only")
        recovered = recover_expired_claims(limit=limit)
        processed = 0
        if options["synthetic"]:
            processed = process_tasks(limit=limit, sender=lambda task: SendResult("SIMULATED"))
        self.stdout.write(f"recovered={recovered} synthetic_processed={processed}")
