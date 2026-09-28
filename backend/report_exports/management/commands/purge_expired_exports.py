"""One-shot private export retention cleanup for schedulers and recovery."""

from django.core.management.base import BaseCommand, CommandError

from report_exports.cleanup import purge_expired_exports


class Command(BaseCommand):
    help = "Remove expired private export files and unreachable export orphans."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        try:
            result = purge_expired_exports(limit=options["limit"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Expired {result['expiredTasks']} tasks; removed {result['removedFiles']} files "
                          f"and {result['removedTemporaryFiles']} temporary files.")
