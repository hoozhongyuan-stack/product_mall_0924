import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import AdminAccount, AdminReadQuota, AccountGroup, GroupPermission, PermissionGroup
from code_versions.models import CodeVersion
from wechat_integration.domain_probe import DomainProbeResult

BASE = '/api/v1/admin/code-release/domain-check'
APP = 'wx0123456789abcdef'
PASSWORD = 'Domain-check-synthetic-password-7!'


class DomainCheckTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = override_settings(MEDIA_ROOT=self.root, WECHAT_MINI_APP_ID=APP,
                                     WECHAT_MINI_APP_SECRET='synthetic-private-secret')
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = AdminAccount.objects.create_user('domain-owner', PASSWORD, kind='OWNER')
        self.client.post('/api/v1/admin/auth/login', {'loginName': 'domain-owner', 'password': PASSWORD},
                         content_type='application/json')
        self.version = self.package()

    def package(self, app_id=APP, api='https://api.example.com/api/v1/app'):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('project.config.json', json.dumps({'appid': app_id}))
            archive.writestr('app.js', 'App({globalData:{apiBaseUrl:' + json.dumps(api) + '}})')
        content = buffer.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / 'code' / (digest + '.zip')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return CodeVersion.objects.create(version_label='source-domain', source_digest=digest,
            package_sha256=digest, package_bytes=len(content), file_count=2, object_key='code/' + path.name)

    def check(self, body=None):
        return self.client.post(BASE, body if body is not None else {'versionId': str(self.version.pk)},
                                content_type='application/json')

    @patch('code_versions.domain_check.check_request_domains')
    def test_selected_package_origin_and_domain_result_are_reported_without_confirmation(self, probe):
        probe.return_value = DomainProbeResult('PASS', 'OK', ('https://api.example.com',))
        result = self.check()
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()['data']
        self.assertEqual(data['status'], 'PASS')
        self.assertEqual(data['versionId'], str(self.version.pk))
        self.assertEqual(data['requiredRequestDomains'], ['https://api.example.com'])
        self.assertEqual(data['missingRequestDomains'], [])
        self.assertIn('no-store', result['Cache-Control'])
        self.assertNotIn('synthetic-private-secret', result.content.decode())
        self.assertEqual(self.check({}).json()['data']['versionId'], str(self.version.pk))

    @patch('code_versions.domain_check.check_request_domains')
    def test_missing_domain_and_unknown_platform_are_distinct(self, probe):
        probe.return_value = DomainProbeResult('PASS', 'OK', ('https://download.example.com',))
        data = self.check().json()['data']
        self.assertEqual((data['status'], data['code']), ('BLOCKED', 'REQUEST_DOMAIN_MISSING'))
        self.assertEqual(data['missingRequestDomains'], ['https://api.example.com'])
        probe.return_value = DomainProbeResult('UNVERIFIED', 'API_PERMISSION_DENIED', platform_error_code=48001)
        data = self.check().json()['data']
        self.assertEqual(data['status'], 'UNVERIFIED')
        self.assertEqual(data['platformErrorCode'], 48001)
        self.assertEqual(data['missingRequestDomains'], [])

    @patch('code_versions.domain_check.check_request_domains')
    def test_bad_configuration_never_calls_platform(self, probe):
        with override_settings(WECHAT_MINI_APP_SECRET=''):
            self.assertEqual(self.check().json()['data']['code'], 'CREDENTIALS_NOT_CONFIGURED')
        for api in ['http://api.example.com', 'https://127.0.0.1', 'https://localhost', 'https://secret@api.example.com']:
            version = self.package(api=api)
            self.assertEqual(self.check({'versionId': str(version.pk)}).json()['data']['status'], 'BLOCKED')
        version = self.package(app_id='wxfedcba9876543210')
        self.assertEqual(self.check({'versionId': str(version.pk)}).json()['data']['code'], 'APP_ID_MISMATCH')
        (self.root / self.version.object_key).write_bytes(b'corrupt')
        self.assertEqual(self.check().json()['data']['code'], 'PACKAGE_UNAVAILABLE')
        probe.assert_not_called()

    @patch('code_versions.domain_check.check_request_domains')
    def test_auth_input_and_shared_rate_limit(self, probe):
        for body in [{'versionId': 'invalid'}, {'versionId': None}, {'extra': 1}]:
            self.assertEqual(self.check(body).status_code, 400)
        self.assertEqual(self.client.get(BASE).status_code, 405)
        self.assertEqual(self.check({'versionId': '00000000-0000-0000-0000-000000000000'}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.check().status_code, 401)
        staff = AdminAccount.objects.create_user('domain-staff', PASSWORD)
        self.client.post('/api/v1/admin/auth/login', {'loginName': staff.login_name, 'password': PASSWORD},
                         content_type='application/json')
        self.assertEqual(self.check().status_code, 403)
        group = PermissionGroup.objects.create(name='domain-read')
        AccountGroup.objects.create(account=staff, group=group)
        GroupPermission.objects.create(group=group, code='code.version.read')
        probe.return_value = DomainProbeResult('PASS', 'OK', ('https://api.example.com',))
        self.assertEqual(self.check().status_code, 200)
        AdminReadQuota.objects.filter(actor=staff).update(count=10)
        response = self.check()
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)

    def test_changed_credentials_or_revoked_session_cannot_report_success(self):
        from wechat_integration.credentials import CredentialSnapshot
        with patch('code_versions.domain_check.check_request_domains', return_value=DomainProbeResult('PASS', 'OK', ('https://api.example.com',))), \
                patch('code_versions.domain_check.effective_credentials', side_effect=[
                    CredentialSnapshot(APP, 'secret', 0), CredentialSnapshot(APP, 'new-secret', 1)]):
            self.assertEqual(self.check().json()['data']['code'], 'CONFIGURATION_CHANGED')
        def revoke(_snapshot):
            AdminAccount.objects.filter(pk=self.owner.pk).update(enabled=False)
            return DomainProbeResult('PASS', 'OK', ('https://api.example.com',))
        with patch('code_versions.domain_check.check_request_domains', side_effect=revoke):
            self.assertEqual(self.check().status_code, 401)

    @patch('code_versions.domain_check.check_request_domains')
    def test_csrf_and_missing_credentials_do_not_call_wechat(self, probe):
        from django.test import Client
        from wechat_integration.models import MiniProgramIntegration
        client = Client(enforce_csrf_checks=True)
        client.get('/api/v1/admin/auth/csrf')
        login = client.post('/api/v1/admin/auth/login', {'loginName': 'domain-owner', 'password': PASSWORD},
                            content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(client.post(BASE, {}, content_type='application/json').status_code, 403)
        MiniProgramIntegration.objects.filter(pk=1).delete()
        data = self.check().json()['data']
        self.assertEqual((data['status'], data['code']), ('BLOCKED', 'CREDENTIALS_UNAVAILABLE'))
        self.assertIsNone(data['appId'])
        probe.assert_not_called()
