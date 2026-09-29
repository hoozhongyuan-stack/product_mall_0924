"""Direct CI upload is a separate, durable fact from source snapshots."""
import hashlib
import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import AdminAccount
from code_versions.models import CodeUploadKey, CodeVersion, ReleaseUploadJob
from code_versions.models import DeveloperUploadKey
from code_versions.release_service import encrypt_developer_upload_key, encrypt_upload_key


APP = 'wx0123456789abcdef'
PASSWORD = 'Upload-test-password-4!'
URL = '/api/v1/admin/code-release/uploads'


class ReleaseUploadTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        secret = self.root / 'fernet.key'
        secret.write_bytes(Fernet.generate_key())
        override = override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=str(secret),
                                     WECHAT_MINI_APP_ID=APP, WECHAT_MINI_APP_SECRET='secret',
                                     MEDIA_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        dispatch = self.root / 'dispatch.token'
        dispatch.write_text('A' * 48)
        token_patch = patch('code_versions.upload_worker.TOKEN_FILE', str(dispatch))
        stage_patch = patch('code_versions.upload_worker.STAGING_ROOT', self.root / 'stage')
        token_patch.start()
        stage_patch.start()
        self.addCleanup(token_patch.stop)
        self.addCleanup(stage_patch.stop)
        self.owner = AdminAccount.objects.create_user('upload-owner', PASSWORD, kind='OWNER')
        self.client.post('/api/v1/admin/auth/login', {'loginName': self.owner.login_name,
                         'password': PASSWORD}, content_type='application/json')
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
        CodeUploadKey.objects.filter(pk=1).update(app_id=APP,
            encrypted_payload=encrypt_upload_key(APP, self.private_key), revision=1)
        self.version = self.package()

    def package(self, *, bad_member=None):
        contents = {
            'project.config.json': json.dumps({'appid': APP}),
            'app.js': "App({globalData:{apiBaseUrl:'https://api.example.com'}})",
            'app.json': '{}',
        }
        if bad_member:
            contents[bad_member] = 'evil'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, value in contents.items():
                archive.writestr(name, value)
        data = buffer.getvalue()
        path = self.root / 'code' / f'{uuid.uuid4().hex}.zip'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return CodeVersion.objects.create(version_label='source-test', source_digest=uuid.uuid4().hex,
            package_sha256=hashlib.sha256(data).hexdigest(), package_bytes=len(data),
            file_count=len(contents), object_key=f'code/{path.name}')

    def confirm(self, channel='CI_DIRECT'):
        target = f'{APP}:{self.version.pk}'
        if channel == 'DIRECT_COMMIT':
            target += ':DIRECT_COMMIT'
        result = self.client.post('/api/v1/admin/auth/confirm', {
            'action': 'code.release.upload', 'objectId': target,
            'revision': 1, 'password': PASSWORD}, content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()['data']['confirmationToken']

    def submit(self, key=None, *, confirmed=True, body=None, channel='CI_DIRECT'):
        headers = {'HTTP_IDEMPOTENCY_KEY': key or str(uuid.uuid4())}
        if confirmed:
            headers['HTTP_X_ACTION_CONFIRMATION'] = self.confirm(channel)
        payload = body or {'versionId': str(self.version.pk), 'version': '1.0.0',
                           'description': 'local test'}
        if channel == 'DIRECT_COMMIT':
            payload = {**payload, 'channel': channel}
        return self.client.post(URL, payload, content_type='application/json', **headers)

    def test_create_and_idempotent_replay_do_not_claim_review_or_release(self):
        key = str(uuid.uuid4())
        first = self.submit(key)
        self.assertEqual(first.status_code, 202, first.content)
        body = first.json()['data']
        self.assertEqual((body['status'], body['channel'], body['reviewAvailable']),
                         ('PENDING', 'CI_DIRECT', False))
        self.assertEqual(self.submit(key, confirmed=False).json()['data']['taskId'], body['taskId'])
        self.assertEqual(ReleaseUploadJob.objects.count(), 1)
        conflict = self.submit(key, confirmed=False, body={'versionId': str(self.version.pk),
            'version': '2.0.0', 'description': 'local test'})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self.client.get(URL + '/' + body['taskId']).json()['data']['taskId'], body['taskId'])
        self.assertEqual(CodeVersion.objects.get(pk=self.version.pk).platform_status, 'NOT_CONFIGURED')

    def test_requires_confirmation_and_blocks_parallel_or_rotated_key(self):
        self.assertEqual(self.submit(confirmed=False).status_code, 403)
        self.assertEqual(self.submit().status_code, 202)
        self.assertEqual(self.submit().status_code, 409)
        CodeUploadKey.objects.filter(pk=1).update(revision=2)
        with patch('code_versions.upload_worker._invoke_adapter') as adapter:
            call_command('run_code_upload_jobs', limit=1)
        adapter.assert_not_called()
        job = ReleaseUploadJob.objects.get()
        self.assertEqual((job.status, job.failure_code), ('FAILED', 'UPLOAD_KEY_CHANGED'))

    def test_worker_success_and_unknown_never_retry(self):
        self.assertEqual(self.submit().status_code, 202)
        with patch('code_versions.upload_worker._invoke_adapter', return_value={'ok': True, 'code': 'UPLOADED'}) as adapter:
            call_command('run_code_upload_jobs', limit=1)
        adapter.assert_called_once()
        job = ReleaseUploadJob.objects.get()
        self.assertEqual(job.status, 'SUCCEEDED')
        self.assertIsNotNone(job.call_started_at)
        self.assertEqual(CodeVersion.objects.get(pk=self.version.pk).platform_status, 'NOT_CONFIGURED')
        self.assertEqual(self.submit().status_code, 202)
        with patch('code_versions.upload_worker._invoke_adapter', side_effect=TimeoutError):
            call_command('run_code_upload_jobs', limit=1)
        self.assertEqual(ReleaseUploadJob.objects.order_by('-created_at').first().status, 'UNKNOWN')
        with patch('code_versions.upload_worker._invoke_adapter') as adapter:
            call_command('run_code_upload_jobs', limit=1)
        adapter.assert_not_called()

    def test_unknown_upload_requires_confirmed_manual_closure_without_success_claim(self):
        self.assertEqual(self.submit().status_code, 202)
        with patch('code_versions.upload_worker._invoke_adapter', side_effect=TimeoutError):
            call_command('run_code_upload_jobs', limit=1)
        job = ReleaseUploadJob.objects.get()
        url = URL + '/' + str(job.pk) + '/resolve'
        note = '已在微信管理后台核查目标版本和上传记录，无法确认此任务成功。'
        self.assertEqual(self.client.post(url, {'note': note}, content_type='application/json').status_code, 403)
        token = self.client.post('/api/v1/admin/auth/confirm', {
            'action': 'code.release.resolve_upload', 'objectId': str(job.pk),
            'revision': 0, 'password': PASSWORD}, content_type='application/json').json()['data']['confirmationToken']
        resolved = self.client.post(url, {'note': note}, content_type='application/json',
                                    HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(resolved.status_code, 200, resolved.content)
        self.assertEqual((resolved.json()['data']['status'], resolved.json()['data']['reviewAvailable']),
                         ('RESOLVED', False))

    def test_bad_archive_fails_before_node_invocation(self):
        self.version = self.package(bad_member='../escape.txt')
        self.assertEqual(self.submit().status_code, 202)
        with patch('code_versions.upload_worker._invoke_adapter') as adapter:
            call_command('run_code_upload_jobs', limit=1)
        adapter.assert_not_called()
        self.assertEqual(ReleaseUploadJob.objects.get().failure_code, 'PACKAGE_INVALID')
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_direct_commit_separates_developer_key_and_stages_overlay(self):
        developer = 'wxabcdef0123456789'
        DeveloperUploadKey.objects.filter(pk=1).update(app_id=developer, revision=1,
            encrypted_payload=encrypt_developer_upload_key(developer, self.private_key))
        with (patch('code_versions.upload_views.developer_app_id', return_value=developer),
              patch('code_versions.upload_views.authorization_status',
                    return_value={'status': 'PASS', 'code': 'AUTHORIZED'}),
              patch('code_versions.upload_worker.developer_app_id', return_value=developer),
              patch('code_versions.upload_worker.authorization_status',
                    return_value={'status': 'PASS', 'code': 'AUTHORIZED'})):
            created = self.submit(channel='DIRECT_COMMIT')
            self.assertEqual(created.status_code, 202, created.content)
            self.assertEqual(created.json()['data']['developerAppId'], developer)
            self.assertFalse(created.json()['data']['reviewAvailable'])

            def inspect(job, project_path, private_key):
                self.assertEqual((job.app_id, job.developer_app_id), (APP, developer))
                self.assertEqual(private_key, self.private_key)
                config = json.loads((project_path / 'project.config.json').read_text())
                self.assertEqual(config['appid'], developer)
                ext = json.loads((project_path / 'ext.json').read_text())
                self.assertEqual(ext, {'extEnable': True, 'extAppid': APP, 'directCommit': True})
                return {'ok': True, 'code': 'UPLOADED'}

            with patch('code_versions.upload_worker._invoke_adapter', side_effect=inspect):
                call_command('run_code_upload_jobs', limit=1)
        job = ReleaseUploadJob.objects.get()
        self.assertEqual((job.channel, job.status), ('DIRECT_COMMIT', 'SUCCEEDED'))
        self.assertNotEqual(job.staged_digest, job.package_sha256)
        self.assertTrue(self.client.get(URL + '/' + str(job.pk)).json()['data']['reviewAvailable'])
        self.assertEqual(CodeVersion.objects.get(pk=self.version.pk).platform_status, 'NOT_CONFIGURED')

    def test_developer_key_is_encrypted_and_never_reused_as_target_key(self):
        developer = 'wxabcdef0123456789'
        confirmation = self.client.post('/api/v1/admin/auth/confirm', {
            'action': 'code.release.developer_upload_key', 'objectId': developer,
            'revision': 0, 'password': PASSWORD}, content_type='application/json')
        self.assertEqual(confirmation.status_code, 200)
        with patch('code_versions.release_views.developer_app_id', return_value=developer):
            saved = self.client.put('/api/v1/admin/code-release/developer-upload-key', {
                'appId': developer, 'key': self.private_key, 'expectedRevision': 0,
            }, content_type='application/json',
                HTTP_X_ACTION_CONFIRMATION=confirmation.json()['data']['confirmationToken'])
        self.assertEqual(saved.status_code, 200, saved.content)
        row = DeveloperUploadKey.objects.get(pk=1)
        self.assertNotIn(self.private_key, row.encrypted_payload)
        self.assertNotIn(self.private_key, saved.content.decode())
        self.assertEqual(CodeUploadKey.objects.get(pk=1).app_id, APP)
        self.assertEqual(self.client.get('/api/v1/admin/code-release/developer-upload-key').json()['data'],
                         {'configured': True, 'appId': developer, 'revision': 1})

    def test_direct_commit_never_enqueues_without_live_code_scope(self):
        developer = 'wxabcdef0123456789'
        DeveloperUploadKey.objects.filter(pk=1).update(app_id=developer, revision=1,
            encrypted_payload=encrypt_developer_upload_key(developer, self.private_key))
        with (patch('code_versions.upload_views.developer_app_id', return_value=developer),
              patch('code_versions.upload_views.authorization_status',
                    return_value={'status': 'UNVERIFIED', 'code': 'PLATFORM_UNAVAILABLE'})):
            denied = self.submit(channel='DIRECT_COMMIT')
        self.assertEqual(denied.status_code, 409)
        self.assertFalse(ReleaseUploadJob.objects.exists())

    def test_platform_rejection_cannot_be_blindly_retried(self):
        self.assertEqual(self.submit().status_code, 202)
        with patch('code_versions.upload_worker._invoke_adapter',
                   return_value={'ok': False, 'code': 'UPLOAD_FAILED'}):
            call_command('run_code_upload_jobs', limit=1)
        self.assertEqual(ReleaseUploadJob.objects.get().status, 'UNKNOWN')
        with patch('code_versions.upload_worker._invoke_adapter') as adapter:
            call_command('run_code_upload_jobs', limit=1)
        adapter.assert_not_called()

    def test_expired_lease_fences_old_worker(self):
        from code_versions.upload_worker import UploadFailure, _finish, _mark_call_started, claim_next, recover_expired
        from django.utils import timezone
        self.assertEqual(self.submit().status_code, 202)
        old = claim_next()
        ReleaseUploadJob.objects.filter(pk=old.pk).update(lease_until=timezone.now())
        recover_expired(timezone.now() + __import__('datetime').timedelta(seconds=1))
        fresh = claim_next()
        self.assertNotEqual(old.lease_token, fresh.lease_token)
        with self.assertRaises(UploadFailure):
            _mark_call_started(old, 'a'*64)
        _finish(old, 'SUCCEEDED')
        self.assertEqual(ReleaseUploadJob.objects.get().status, 'RUNNING')

    def test_adapter_http_response_is_bounded_and_local_error_is_distinct(self):
        from code_versions.upload_worker import UploadFailure, _invoke_adapter
        self.assertEqual(self.submit().status_code, 202)
        job = ReleaseUploadJob.objects.get()
        from unittest.mock import Mock
        fake = Mock()
        fake.getresponse.return_value.status = 200
        fake.getresponse.return_value.read.return_value = b'X' * 5000
        with patch('code_versions.upload_worker.http.client.HTTPConnection', return_value=fake):
            with self.assertRaises(UploadFailure) as caught:
                _invoke_adapter(job, self.root, self.private_key)
        self.assertEqual(caught.exception.code, 'RESULT_UNKNOWN')
        fake.getresponse.return_value.read.return_value = b'{"ok":false,"code":"PROJECT_INVALID"}'
        with patch('code_versions.upload_worker.http.client.HTTPConnection', return_value=fake):
            self.assertEqual(_invoke_adapter(job, self.root, self.private_key)['code'],
                             'PROJECT_INVALID')
        args, kwargs = fake.request.call_args
        self.assertEqual((args[0], args[1]), ('POST', '/upload'))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer ' + 'A' * 48)
