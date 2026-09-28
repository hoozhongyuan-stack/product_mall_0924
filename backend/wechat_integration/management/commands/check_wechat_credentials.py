"""Read-only release gate: verify configuration identity and restored key pairing."""
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from wechat_integration.credentials import CredentialsUnavailable, effective_credentials


class Command(BaseCommand):
    help = 'Verify the migrated credential singleton and managed decryption without platform calls.'
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            snapshot = effective_credentials()
        except (CredentialsUnavailable, DatabaseError):
            raise CommandError('WECHAT_CREDENTIALS_UNAVAILABLE: check migrations and the original deployment key.') from None
        self.stdout.write(f'WECHAT_CREDENTIALS_READY source={snapshot.source}')
