"""Run bounded export work once or keep a deployment-owned worker alive."""

import time

from django.core.management.base import BaseCommand, CommandError

from report_exports.cleanup import purge_expired_exports
from report_exports.worker import run_next


class Command(BaseCommand):
    help = "Generate pending private CSV exports; --watch also maintains 24-hour retention."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--interval", type=int, default=5)

    def handle(self, *args, **options):
        limit, interval = options["limit"], options["interval"]
        if not 1 <= limit <= 100 or not 1 <= interval <= 60:
            raise CommandError("limit must be 1-100 and interval must be 1-60")
        while True:
            cleanup = purge_expired_exports(limit=100)
            count = 0
            for _ in range(limit):
                if run_next() is None:
                    break
                count += 1
            if not options["watch"]:
                self.stdout.write(f"Processed {count} export jobs; expired {cleanup['expiredTasks']} tasks; "
                                  f"removed {cleanup['removedFiles']} files and "
                                  f"{cleanup['removedTemporaryFiles']} temporary files.")
                return
            if count == 0:
                try:
                    time.sleep(interval)
                except KeyboardInterrupt:
                    return
