"""Run bounded direct CI upload jobs outside web request workers."""
import time

from django.core.management.base import BaseCommand, CommandError

from code_versions.upload_worker import claim_next, recover_expired, run_one


class Command(BaseCommand):
    help = 'Process direct CI upload jobs without treating them as review or publication.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=10)
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--interval', type=float, default=5)

    def handle(self, *args, **options):
        limit, interval = options['limit'], options['interval']
        if not 1 <= limit <= 100 or not 1 <= interval <= 60:
            raise CommandError('limit or interval is outside the allowed range.')
        while True:
            recover_expired()
            completed = 0
            for _ in range(limit):
                job = claim_next()
                if not job:
                    break
                run_one(job)
                completed += 1
            if not options['watch']:
                self.stdout.write(f'processed={completed}')
                return
            time.sleep(interval)
