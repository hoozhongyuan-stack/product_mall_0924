"""Recover interrupted local material writes and deletions without upload."""
from django.core.management.base import BaseCommand, CommandError

from catalog.storage import recover_media_storage
from catalog.validation import CatalogError


class Command(BaseCommand):
    help = "Reconcile durable media intents and remove upload temporaries older than 24 hours."

    def handle(self, *args, **options):
        try:
            result = recover_media_storage()
        except CatalogError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Recovered {result['recoveredIntents']} media intents; "
            f"removed {result['temporaryFilesRemoved']} expired upload temporaries.")
