"""Read-only release gate: verify configuration identity and restored key pairing."""
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from wechat_integration.credentials import CredentialsUnavailable, effective_credentials
from code_versions.models import CodeUploadKey, DeveloperUploadKey
from code_versions.release_service import (UploadKeyUnavailable, decrypt_developer_upload_key,
                                           decrypt_upload_key)
from wechat_open_platform.crypto import unseal
from wechat_open_platform.models import AuthorizerGrant, ComponentConfig


class Command(BaseCommand):
    help = 'Verify the migrated credential singleton and managed decryption without platform calls.'
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            snapshot = effective_credentials()
            upload_key = CodeUploadKey.objects.get(pk=1)
            if upload_key.encrypted_payload:
                decrypt_upload_key(upload_key)
            developer_key = DeveloperUploadKey.objects.get(pk=1)
            if developer_key.encrypted_payload:
                decrypt_developer_upload_key(developer_key)
            component = ComponentConfig.objects.get(pk=1)
            for field, purpose in [('encrypted_credentials', 'open-platform-credentials'),
                                   ('encrypted_ticket', 'component-ticket'),
                                   ('encrypted_access_token', 'component-access-token')]:
                if value := getattr(component, field):
                    unseal(purpose, value)
            for grant in AuthorizerGrant.objects.all():
                for field, purpose in [('encrypted_refresh_token', 'authorizer-refresh-token'),
                                       ('encrypted_access_token', 'authorizer-access-token')]:
                    if value := getattr(grant, field):
                        unseal(purpose, value)
        except (CredentialsUnavailable, UploadKeyUnavailable, CodeUploadKey.DoesNotExist,
                DeveloperUploadKey.DoesNotExist, ComponentConfig.DoesNotExist, DatabaseError):
            raise CommandError('WECHAT_CREDENTIALS_UNAVAILABLE: check migrations and the original deployment key.') from None
        self.stdout.write(f'WECHAT_CREDENTIALS_READY source={snapshot.source}')
