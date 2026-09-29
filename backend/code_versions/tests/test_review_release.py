"""Audit and release attempts are persisted before any third-party side effect."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import AdminAccount
from code_versions.models import CodeVersion, ReleaseReviewJob, ReleaseUploadJob
from wechat_open_platform.client import PlatformUnavailable


APP = 'wx0123456789abcdef'
BASE = '/api/v1/admin/code-release'
PASSWORD = 'Review-test-password-4!'
CATEGORY = {'first_class': '商家自营', 'second_class': '百货',
            'first_id': 1, 'second_id': 2}


class ReviewReleaseTests(TestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user('review-owner', PASSWORD, kind='OWNER')
        self.client.post('/api/v1/admin/auth/login', {'loginName': self.owner.login_name,
                         'password': PASSWORD}, content_type='application/json')
        version = CodeVersion.objects.create(version_label='source-review', source_digest='a'*64,
            package_sha256='b'*64, package_bytes=123, file_count=3, object_key='review/package.zip')
        self.upload = ReleaseUploadJob.objects.create(version=version, app_id=APP,
            developer_app_id='wxabcdef0123456789', channel='DIRECT_COMMIT',
            package_sha256=version.package_sha256, key_revision=1, upload_version='1.0.0',
            description='review test', request_key=uuid.uuid4(), request_hash='c'*64,
            created_by=self.owner, status='SUCCEEDED')

    def confirm(self, action, object_id, revision):
        result = self.client.post('/api/v1/admin/auth/confirm', {'action': action,
            'objectId': object_id, 'revision': revision, 'password': PASSWORD},
            content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()['data']['confirmationToken']

    def submit(self, *, key=None, confirmed=True):
        headers = {'HTTP_IDEMPOTENCY_KEY': key or str(uuid.uuid4())}
        if confirmed:
            headers['HTTP_X_ACTION_CONFIRMATION'] = self.confirm(
                'code.release.review', str(self.upload.pk), 0)
        with (patch('code_versions.review_views.authorization_status', return_value={'status': 'PASS'}),
              patch('code_versions.review_views.get_category', return_value=[CATEGORY]),
              patch('code_versions.review_views.submit_audit', return_value=12345) as remote):
            result = self.client.post(BASE + '/reviews', {'uploadTaskId': str(self.upload.pk),
                'itemList': [CATEGORY], 'versionDesc': '本次功能更新'},
                content_type='application/json', **headers)
        return result, remote

    def test_submit_refresh_and_release_guarded_by_confirmation(self):
        denied, remote = self.submit(confirmed=False)
        self.assertEqual(denied.status_code, 403)
        remote.assert_not_called()
        key = str(uuid.uuid4())
        created, remote = self.submit(key=key)
        self.assertEqual(created.status_code, 202, created.content)
        remote.assert_called_once()
        job = ReleaseReviewJob.objects.get()
        self.assertEqual((job.status, job.audit_id), ('SUBMITTED', 12345))
        replay, remote = self.submit(key=key, confirmed=False)
        self.assertEqual(replay.status_code, 200)
        remote.assert_not_called()
        self.assertEqual(self.client.post(BASE + f'/reviews/{job.pk}/release', {},
                         content_type='application/json').status_code, 409)
        with patch('code_versions.review_views.get_audit_status',
                   return_value={'status': 0, 'auditid': 12345, 'reason': ''}):
            refreshed = self.client.post(BASE + f'/reviews/{job.pk}/refresh', {},
                content_type='application/json')
        self.assertEqual(refreshed.json()['data']['status'], 'APPROVED')
        confirmation = self.confirm('code.release.publish', str(job.pk), 12345)
        with patch('code_versions.review_views.release_approved', return_value={}) as release:
            published = self.client.post(BASE + f'/reviews/{job.pk}/release', {},
                content_type='application/json', HTTP_X_ACTION_CONFIRMATION=confirmation)
        self.assertEqual(published.status_code, 202, published.content)
        self.assertEqual(published.json()['data']['status'], 'RELEASE_REQUESTED')
        release.assert_called_once_with(APP, auditid=12345, user_version='1.0.0')
        blocked, remote = self.submit()
        self.assertEqual(blocked.status_code, 409)
        remote.assert_not_called()
        ReleaseReviewJob.objects.filter(pk=job.pk).update(updated_at=timezone.now() - timedelta(minutes=6))
        token = self.confirm('code.release.resolve_review', str(job.pk), 0)
        closed = self.client.post(BASE + f'/reviews/{job.pk}/resolve', {
            'note': '已在微信后台与真机核查本次发布状态，并人工结束本次任务。'},
            content_type='application/json', HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual(closed.json()['data']['status'], 'CLOSED_UNVERIFIED')

    def test_uncertain_submit_blocks_second_attempt_without_remote_retry(self):
        confirmation = self.confirm('code.release.review', str(self.upload.pk), 0)
        with (patch('code_versions.review_views.authorization_status', return_value={'status': 'PASS'}),
              patch('code_versions.review_views.get_category', return_value=[CATEGORY]),
              patch('code_versions.review_views.submit_audit', side_effect=PlatformUnavailable()) as remote):
            first = self.client.post(BASE + '/reviews', {'uploadTaskId': str(self.upload.pk),
                'itemList': [CATEGORY], 'versionDesc': '本次功能更新'}, content_type='application/json',
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()), HTTP_X_ACTION_CONFIRMATION=confirmation)
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(first.json()['data']['status'], 'UNKNOWN')
        remote.assert_called_once()
        blocked, remote = self.submit()
        self.assertEqual(blocked.status_code, 409)
        remote.assert_not_called()
        job = ReleaseReviewJob.objects.get()
        token = self.confirm('code.release.resolve_review', str(job.pk), 0)
        note = '已在微信管理后台核查审核记录，未能确认此任务是否已提交。'
        closed = self.client.post(BASE + f'/reviews/{job.pk}/resolve', {'note': note},
            content_type='application/json', HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual(closed.json()['data']['status'], 'CLOSED_UNVERIFIED')
        self.assertEqual(self.client.post(BASE + f'/reviews/{job.pk}/release', {},
                         content_type='application/json').status_code, 409)

    def test_category_gate_blocks_unverified_authorization(self):
        with patch('code_versions.review_views.authorization_status',
                   return_value={'status': 'UNVERIFIED'}):
            result = self.client.get(BASE + '/categories')
        self.assertEqual(result.status_code, 503)

    def test_review_rejects_older_upload_after_newer_target_upload(self):
        ReleaseUploadJob.objects.create(version=self.upload.version, app_id=APP,
            developer_app_id=self.upload.developer_app_id, channel='DIRECT_COMMIT',
            package_sha256=self.upload.package_sha256, key_revision=1,
            upload_version='1.0.1', description='newer', request_key=uuid.uuid4(),
            request_hash='d'*64, created_by=self.owner, status='SUCCEEDED')
        result, remote = self.submit()
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(result.json()['error']['code'], 'NEWER_UPLOAD_EXISTS')
        remote.assert_not_called()
