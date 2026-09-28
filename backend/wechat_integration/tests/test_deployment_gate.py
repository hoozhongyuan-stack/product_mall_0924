"""Release readiness must prove restored managed ciphertext matches the mounted key."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from wechat_integration.credentials import encrypt_credentials
from wechat_integration.models import MiniProgramIntegration


class DeploymentCredentialGateTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key = Path(self.directory.name) / 'credential-key'
        self.key.write_bytes(Fernet.generate_key())
        settings = override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=str(self.key),
                                     WECHAT_MINI_APP_ID='environment-app', WECHAT_MINI_APP_SECRET='environment-secret')
        settings.enable()
        self.addCleanup(settings.disable)

    def managed(self):
        ciphertext = encrypt_credentials('wx0123456789abcdef', 'synthetic-private-application-secret')
        MiniProgramIntegration.objects.filter(pk=1).update(managed=True, encrypted_payload=ciphertext)
        return ciphertext

    def run_gate(self):
        output = StringIO()
        call_command('check_wechat_credentials', stdout=output)
        return output.getvalue()

    def test_initial_unmanaged_environment_is_ready_without_creating_or_changing_data(self):
        before = list(MiniProgramIntegration.objects.values())
        with override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE='', WECHAT_MINI_APP_ID='', WECHAT_MINI_APP_SECRET=''):
            with CaptureQueriesContext(connection) as queries:
                result = self.run_gate()
        self.assertEqual(result, 'WECHAT_CREDENTIALS_READY source=ENV\n')
        self.assertEqual(list(MiniProgramIntegration.objects.values()), before)
        self.assertTrue(queries)
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))

    def test_matching_key_is_read_only_and_never_calls_a_platform(self):
        ciphertext = self.managed()
        before = list(MiniProgramIntegration.objects.values())
        with patch('wechat_integration.probe._transport') as transport, CaptureQueriesContext(connection) as queries:
            result = self.run_gate()
        transport.assert_not_called()
        self.assertEqual(result, 'WECHAT_CREDENTIALS_READY source=MANAGED\n')
        self.assertEqual(list(MiniProgramIntegration.objects.values()), before)
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))
        for value in [ciphertext, 'synthetic-private-application-secret', self.key.read_text()]:
            self.assertNotIn(value, result)

    def test_another_well_formed_key_is_not_a_successful_restore(self):
        ciphertext = self.managed()
        self.key.write_bytes(Fernet.generate_key())
        output = StringIO()
        with self.assertRaises(CommandError) as error:
            call_command('check_wechat_credentials', stdout=output)
        combined = output.getvalue() + str(error.exception)
        self.assertIn('WECHAT_CREDENTIALS_UNAVAILABLE', combined)
        for value in [ciphertext, 'synthetic-private-application-secret', self.key.read_text()]:
            self.assertNotIn(value, combined)
        self.assertNotIn('READY', combined)

    def test_managed_missing_key_corrupt_payload_and_missing_row_fail_closed(self):
        self.managed()
        with override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=''):
            with self.assertRaises(CommandError):
                self.run_gate()
        MiniProgramIntegration.objects.filter(pk=1).update(encrypted_payload='corrupted-secret-like-content')
        with self.assertRaises(CommandError) as error:
            self.run_gate()
        self.assertNotIn('corrupted-secret-like-content', str(error.exception))
        MiniProgramIntegration.objects.all().delete()
        with self.assertRaises(CommandError):
            self.run_gate()
        self.assertFalse(MiniProgramIntegration.objects.exists())

    def test_database_or_schema_errors_are_not_printed_and_do_not_become_environment_fallback(self):
        with patch('wechat_integration.credentials.integration_row', side_effect=DatabaseError('private-database-detail')):
            with self.assertRaises(CommandError) as error:
                self.run_gate()
        self.assertNotIn('private-database-detail', str(error.exception))
        self.assertIn('WECHAT_CREDENTIALS_UNAVAILABLE', str(error.exception))
