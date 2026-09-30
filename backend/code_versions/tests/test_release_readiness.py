"""Release preflight and private upload-key boundaries."""
import io
import json
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (Encoding, NoEncryption, PrivateFormat)
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import (AccountGroup, AdminAccount, AuditLog, GroupPermission,
                             PermissionGroup)
from code_versions.models import CodeUploadKey, CodeVersion


APP = 'wx0123456789abcdef'
PASSWORD = 'Readiness-test-password-4!'
BASE = '/api/v1/admin/code-release'


class ReadinessTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        key_file = self.root / 'fernet.key'
        key_file.write_bytes(Fernet.generate_key())
        settings = override_settings(
            MALL_WECHAT_CREDENTIAL_KEY_FILE=str(key_file), WECHAT_MINI_APP_ID=APP,
            WECHAT_MINI_APP_SECRET='test-secret', MEDIA_ROOT=self.root)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = AdminAccount.objects.create_user('release-owner', PASSWORD, kind='OWNER')
        self.client.post('/api/v1/admin/auth/login', {'loginName': 'release-owner',
                         'password': PASSWORD}, content_type='application/json')
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()

    def confirm(self, revision=0):
        result = self.client.post('/api/v1/admin/auth/confirm', {
            'action': 'code.release.upload_key', 'objectId': APP,
            'revision': revision, 'password': PASSWORD}, content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()['data']['confirmationToken']

    def save(self, revision=0, **extra):
        body = {'appId': APP, 'key': self.private_key, 'expectedRevision': revision, **extra}
        return self.client.put(BASE + '/upload-key', body, content_type='application/json',
                               HTTP_X_ACTION_CONFIRMATION=self.confirm(revision))

    def package(self, app_id=APP, api='https://api.example.com'):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('project.config.json', json.dumps({'appid': app_id}))
            archive.writestr('app.js', "App({globalData: {apiBaseUrl: '" + api + "'}})")
        data = buffer.getvalue()
        import hashlib
        path = self.root / 'code' / 'package.zip'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return CodeVersion.objects.create(version_label='source-test', source_digest='a'*64,
            package_sha256=hashlib.sha256(data).hexdigest(), package_bytes=len(data),
            file_count=2, object_key='code/package.zip')

    def test_readiness_reports_distinct_checks_without_claiming_platform_authorization(self):
        self.package()
        with override_settings(WECHAT_CODE_UPLOAD_EGRESS_IP='8.152.204.21'):
            result = self.client.get(BASE + '/readiness')
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()['data']
        checks = {item['code']: item['status'] for item in data['checks']}
        self.assertEqual(checks['APP_ID'], 'PASS')
        self.assertEqual(checks['APP_SECRET'], 'UNVERIFIED')
        self.assertEqual(checks['SOURCE_PACKAGE'], 'PASS')
        self.assertEqual(checks['RELEASE_CONFIG'], 'PASS')
        self.assertEqual(checks['UPLOAD_KEY'], 'BLOCKED')
        self.assertEqual(checks['PLATFORM_INTEGRATION'], 'BLOCKED')
        self.assertEqual(checks['THIRD_PARTY_AUTH'], 'BLOCKED')
        self.assertFalse(data['uploadKey']['configured'])
        self.assertEqual(data['egressIp'], '8.152.204.21')
        self.assertNotIn('test-secret', result.content.decode())

    def test_upload_key_encrypted_revisioned_and_no_plaintext_output(self):
        self.package()
        result = self.save()
        self.assertEqual(result.status_code, 200, result.content)
        row = CodeUploadKey.objects.get(pk=1)
        self.assertEqual((row.revision, row.app_id), (1, APP))
        self.assertNotIn('BEGIN PRIVATE KEY', row.encrypted_payload)
        self.assertNotIn(self.private_key, result.content.decode())
        self.assertEqual(self.client.get(BASE + '/upload-key').json()['data'],
                         {'configured': True, 'revision': 1, 'appId': APP})
        checks = {item['code']: item['status'] for item in
                  self.client.get(BASE + '/readiness').json()['data']['checks']}
        self.assertEqual(checks['UPLOAD_KEY'], 'PASS')
        self.assertEqual(checks['THIRD_PARTY_AUTH'], 'BLOCKED')
        self.assertNotIn(self.private_key, json.dumps(list(AuditLog.objects.values('before', 'after'))))
        self.assertEqual(self.save(revision=0).status_code, 409)

    def test_rejects_invalid_key_wrong_app_and_missing_confirmation(self):
        self.assertEqual(self.save(key='not-a-key').status_code, 400)
        self.assertEqual(self.save(appId='wxfedcba9876543210').status_code, 409)
        response = self.client.put(BASE + '/upload-key', {'appId': APP,
            'key': self.private_key, 'expectedRevision': 0}, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CodeUploadKey.objects.get(pk=1).revision, 0)
        self.assertFalse(CodeUploadKey.objects.get(pk=1).encrypted_payload)

    def test_source_mismatch_and_corrupt_key_are_blocked(self):
        self.package(app_id='touristappid', api='http://127.0.0.1:8000')
        checks = {item['code']: item['status'] for item in
                  self.client.get(BASE + '/readiness').json()['data']['checks']}
        self.assertEqual(checks['RELEASE_CONFIG'], 'BLOCKED')
        self.assertEqual(self.save().status_code, 200)
        CodeUploadKey.objects.filter(pk=1).update(encrypted_payload='corrupt')
        checks = {item['code']: item['status'] for item in
                  self.client.get(BASE + '/readiness').json()['data']['checks']}
        self.assertEqual(checks['UPLOAD_KEY'], 'BLOCKED')
        with self.assertRaises(CommandError):
            call_command('check_wechat_credentials')

    def test_readiness_is_rate_limited_before_repeated_package_hashing(self):
        self.package()
        for _ in range(10):
            self.assertEqual(self.client.get(BASE + '/readiness').status_code, 200)
        limited = self.client.get(BASE + '/readiness')
        self.assertEqual(limited.status_code, 429)
        self.assertIn('Retry-After', limited)

    def test_missing_integration_row_is_blocked_without_falling_back_to_environment(self):
        from wechat_integration.models import MiniProgramIntegration
        MiniProgramIntegration.objects.filter(pk=1).delete()
        result = self.client.get(BASE + '/readiness')
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()['data']['appId'])
        checks = {item['code']: item['status'] for item in result.json()['data']['checks']}
        self.assertEqual(checks['APP_ID'], 'BLOCKED')
        self.assertEqual(checks['APP_SECRET'], 'BLOCKED')
        self.assertEqual(self.save().status_code, 503)

    def test_read_access_does_not_allow_key_management_and_csrf_is_required(self):
        from django.test import Client
        group = PermissionGroup.objects.create(code='release-readers', name='发布只读')
        staff = AdminAccount.objects.create_user('release-reader', PASSWORD, display_name='只读')
        AccountGroup.objects.create(account=staff, group=group)
        GroupPermission.objects.create(group=group, code='code.version.read')
        client = Client(enforce_csrf_checks=True)
        client.get('/api/v1/admin/auth/csrf')
        login = client.post('/api/v1/admin/auth/login', {'loginName': staff.login_name,
            'password': PASSWORD}, content_type='application/json',
            HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(login.status_code, 200, login.content)
        self.assertEqual(client.get(BASE + '/readiness').status_code, 200)
        self.assertEqual(client.get(BASE + '/upload-key').status_code, 403)
        GroupPermission.objects.create(group=group, code='code.release.manage')
        self.assertEqual(client.get(BASE + '/upload-key').status_code, 200)
        without_csrf = client.put(BASE + '/upload-key', {'appId': APP,
            'key': self.private_key, 'expectedRevision': 0}, content_type='application/json')
        self.assertEqual(without_csrf.status_code, 403)

    def test_platform_probe_unavailable_is_unverified_and_missing_key_row_is_503(self):
        from wechat_integration.credentials import effective_credentials, snapshot_fingerprint
        from wechat_integration.models import MiniProgramIntegration
        snapshot = effective_credentials()
        MiniProgramIntegration.objects.filter(pk=1).update(
            last_check_revision=snapshot.revision, last_check_at=timezone.now(),
            last_check_status='UNAVAILABLE', last_check_code='PLATFORM_UNAVAILABLE',
            last_check_fingerprint=snapshot_fingerprint(snapshot))
        checks = {item['code']: item['status'] for item in
                  self.client.get(BASE + '/readiness').json()['data']['checks']}
        self.assertEqual(checks['APP_SECRET'], 'UNVERIFIED')
        CodeUploadKey.objects.filter(pk=1).delete()
        missing = self.save()
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(missing.json()['error']['code'], 'CONFIGURATION_UNAVAILABLE')

    def test_expired_successful_secret_probe_is_unverified(self):
        from wechat_integration.credentials import effective_credentials, snapshot_fingerprint
        from wechat_integration.models import MiniProgramIntegration
        snapshot = effective_credentials()
        MiniProgramIntegration.objects.filter(pk=1).update(
            last_check_revision=snapshot.revision,
            last_check_at=timezone.now() - timedelta(hours=25),
            last_check_status='SUCCESS', last_check_code='OK',
            last_check_fingerprint=snapshot_fingerprint(snapshot))
        checks = {item['code']: item for item in
                  self.client.get(BASE + '/readiness').json()['data']['checks']}
        self.assertEqual(checks['APP_SECRET']['status'], 'UNVERIFIED')
        self.assertIn('超过 24 小时', checks['APP_SECRET']['detail'])
        self.assertIn('上次检测', checks['APP_SECRET']['detail'])

    def test_local_domains_do_not_pass_release_configuration(self):
        for host in ('api.local', 'foo.localhost', 'localhost.', 'api.internal', 'api.test'):
            with self.subTest(host=host):
                from code_versions.release_service import _public_https
                self.assertFalse(_public_https('https://' + host))
