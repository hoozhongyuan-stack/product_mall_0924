from unittest.mock import patch
from django.test import RequestFactory, SimpleTestCase
from django.core import signing
from code_versions import views

class CodeListFilterTests(SimpleTestCase):
    def test_version_keyword_is_applied_before_cursor_pagination(self):
        request = RequestFactory().get('/code-versions', {'q': 'release-7', 'limit': '50'})
        with patch.object(views, 'require', return_value=(None, None)), patch.object(views, '_versions') as query, patch.object(views, '_page', return_value=({'items': [], 'nextCursor': None}, None)) as page:
            result = views.versions_view(request)
        self.assertEqual(result.status_code, 200)
        query.return_value.filter.assert_called_once()
        self.assertEqual(page.call_args.args[1], query.return_value.filter.return_value)
        self.assertIn('release-7', str(query.return_value.filter.call_args.args[0]))

    def test_job_status_is_validated_and_applied_server_side(self):
        request = RequestFactory().get('/code-sync-jobs', {'status': 'FAILED', 'q': 'SOURCE_INVALID'})
        with patch.object(views, 'require', return_value=(None, None)), patch.object(views.CodeBuildJob, 'objects') as manager, patch.object(views, '_page', return_value=({'items': [], 'nextCursor': None}, None)):
            result = views.jobs_view(request)
        self.assertEqual(result.status_code, 200)
        self.assertIn('FAILED', str(manager.all.return_value.filter.call_args))
        for params in [{'q': 'x' * 101}, {'status': 'INVALID'}, {'status': 'FAILED', 'unexpected': 'value'}]:
            with patch.object(views, 'require', return_value=(None, None)):
                result = views.jobs_view(RequestFactory().get('/code-sync-jobs', params))
            self.assertEqual(result.status_code, 400)

    def test_filtered_cursor_cannot_be_reused_for_other_filters(self):
        first = RequestFactory().get('/code-sync-jobs', {'q': 'one'})
        second = RequestFactory().get('/code-sync-jobs', {'q': 'two'})
        self.assertNotEqual(views._filter_salt(first, 'code-job-list'), views._filter_salt(second, 'code-job-list'))
        self.assertEqual(views._filter_salt(RequestFactory().get('/code-sync-jobs'), 'code-job-list'), 'code-job-list')

from django.test import TestCase
from django.utils import timezone
from accounts.models import AdminAccount
from code_versions.models import CodeVersion, CodeBuildJob
import json
import secrets

class CodeListFilterIntegrationTests(TestCase):
    def setUp(self):
        password = secrets.token_urlsafe(24)
        account = AdminAccount.objects.create_user('list-code-owner', password, kind='OWNER', display_name='主账号')
        self.client.get('/api/v1/admin/auth/csrf')
        result = self.client.post('/api/v1/admin/auth/login', data=json.dumps({'loginName': account.login_name, 'password': password}), content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(result.status_code, 200)

    def test_keyword_filtered_versions_keep_real_cursor_and_reject_changed_query(self):
        matching = []
        for index, label in enumerate(['alpha-1', 'beta', 'alpha-2', 'alpha-3'], 1):
            version = CodeVersion.objects.create(version_label=label, source_digest=f'{index:064x}', package_sha256=f'{index+10:064x}', package_bytes=100, file_count=1, object_key=f'code/list-test-{index}.zip')
            if label.startswith('alpha'):
                matching.append(str(version.pk))
        first = self.client.get('/api/v1/admin/code-versions', {'q': 'alpha', 'limit': 2})
        self.assertEqual(first.status_code, 200, first.content)
        cursor = first.json()['data']['nextCursor']
        self.assertTrue(cursor)
        second = self.client.get('/api/v1/admin/code-versions', {'q': 'alpha', 'limit': 2, 'cursor': cursor})
        self.assertEqual(second.status_code, 200, second.content)
        all_ids = [row['versionId'] for row in first.json()['data']['items'] + second.json()['data']['items']]
        self.assertCountEqual(all_ids, matching)
        self.assertEqual(self.client.get('/api/v1/admin/code-versions', {'q': 'beta', 'cursor': cursor}).status_code, 400)

    def test_sync_status_and_keyword_filter_before_paging(self):
        CodeBuildJob.objects.create(status='FAILED', failure_code='SOURCE_INVALID', completed_at=timezone.now())
        CodeBuildJob.objects.create(status='FAILED', failure_code='STORAGE_ERROR', completed_at=timezone.now())
        CodeBuildJob.objects.create(status='STARTED')
        result = self.client.get('/api/v1/admin/code-sync-jobs', {'q': 'SOURCE', 'status': 'FAILED', 'limit': 50})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual([row['failureCode'] for row in result.json()['data']['items']], ['SOURCE_INVALID'])
