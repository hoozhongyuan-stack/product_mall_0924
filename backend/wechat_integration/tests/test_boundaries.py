import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import AdminReadQuota, AuditLog, GroupPermission, PermissionGroup
from .test_api import APP, SECRET, CredentialApiBase


class InputAndPermissionTests(CredentialApiBase):
    def test_structured_secret_action_is_validation_error_not_server_error(self):
        for action in [[], {}]:
            result = self.client.put('/api/v1/admin/integrations/wechat-mini-program', {
                'expectedRevision': 0, 'appId': APP, 'secretAction': action}, content_type='application/json')
            self.assertEqual(result.status_code, 400, result.content)

    def test_json_size_invalid_encoding_and_secret_bounds(self):
        base = '/api/v1/admin/integrations/wechat-mini-program'
        for body in [b'\xff', b'{', b'[]', b' ' * 8193]:
            self.assertEqual(self.client.put(base, body, content_type='application/json').status_code, 400)
        for secret in ['', '   ', 'x' * 257, 'a\nb', 'a\x80b', 123]:
            self.assertEqual(self.save(secret=secret).status_code, 400)
        opaque = ' unchanged whitespace at edges '
        self.assertEqual(self.save(secret=opaque).status_code, 200)
        from wechat_integration.credentials import effective_credentials
        self.assertEqual(effective_credentials().secret, opaque)

    def test_read_write_and_probe_quotas(self):
        base = '/api/v1/admin/integrations/wechat-mini-program'
        AdminReadQuota.objects.create(actor=self.owner, scope='wechat.integration', count=30,
            window_start=timezone.now().replace(second=0, microsecond=0))
        limited = self.client.get(base)
        self.assertEqual(limited.status_code, 429)
        self.assertGreater(int(limited['Retry-After']), 0)
        for action, count in [('update', 30), ('test', 5)]:
            AuditLog.objects.bulk_create([AuditLog(actor=self.owner, action_code='wechat.integration.'+action,
                object_type='wechat_integration', request_id=uuid.uuid4()) for _ in range(count)])
            if action == 'update':
                result = self.save()
            else:
                result = self.client.post(base+'/test', {'expectedRevision': 0}, content_type='application/json',
                    HTTP_X_ACTION_CONFIRMATION=self.confirmation(action='test'))
            self.assertEqual(result.status_code, 429, result.content)

    def test_environment_check_survives_reads_but_not_environment_rotation(self):
        from wechat_integration.probe import ProbeResult
        base = '/api/v1/admin/integrations/wechat-mini-program'
        with patch('wechat_integration.views.check_credentials', return_value=ProbeResult('SUCCESS', 'OK')):
            result = self.client.post(base+'/test', {'expectedRevision': 0}, content_type='application/json',
                HTTP_X_ACTION_CONFIRMATION=self.confirmation(action='test'))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get(base).json()['data']['lastCheck']['status'], 'SUCCESS')
        with override_settings(WECHAT_MINI_APP_SECRET='rotated-env'):
            self.assertIsNone(self.client.get(base).json()['data']['lastCheck'])

    def test_confirmation_expiry_wrong_object_cross_session_and_readonly(self):
        from accounts.models import ActionConfirmation, AdminAccount
        from .test_api import PASSWORD
        base = '/api/v1/admin/integrations/wechat-mini-program'
        token = self.confirmation(objectId='wrong')
        self.assertEqual(self.save(token=token).status_code, 403)
        token = self.confirmation()
        ActionConfirmation.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.save(token=token).status_code, 403)
        token = self.confirmation()
        other = Client()
        other.post('/api/v1/admin/auth/login', {'loginName': self.owner.login_name, 'password': PASSWORD},
                   content_type='application/json')
        self.assertEqual(other.put(base, {'expectedRevision': 0, 'appId': APP, 'secretAction': 'REPLACE',
            'appSecret': SECRET}, content_type='application/json', HTTP_X_ACTION_CONFIRMATION=token).status_code, 403)
        staff = AdminAccount.objects.create_user('reader', PASSWORD)
        group = PermissionGroup.objects.create(code='wechat-reader', name='微信只读')
        GroupPermission.objects.create(group=group, code='wechat.integration.read')
        staff.permission_groups.add(group)
        other.post('/api/v1/admin/auth/logout')
        other.post('/api/v1/admin/auth/login', {'loginName': staff.login_name, 'password': PASSWORD},
                   content_type='application/json')
        self.assertEqual(other.get(base).status_code, 200)
        self.assertEqual(other.put(base, '{}', content_type='application/json').status_code, 403)
        self.assertEqual(other.post(base+'/test', '{}', content_type='application/json').status_code, 403)
