from django.core.management.base import BaseCommand

from catalog.media import purge_expired_orphans


class Command(BaseCommand):
    help = "Delete unbound product media older than 24 hours. Run daily in deployment."

    def handle(self, *args, **options):
        count = purge_expired_orphans()
        self.stdout.write(f"Removed {count} expired unbound media assets.")
