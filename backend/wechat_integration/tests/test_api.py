"""Real HTTP contracts for private application credentials; no platform calls."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import Client, TestCase, override_settings

from accounts.models import AdminAccount, AuditLog
from catalog.models import MemberGrade
from customers.models import Member, MemberSession, WechatCodeUse

BASE = '/api/v1/admin/integrations/wechat-mini-program'
APP = 'wx0123456789abcdef'
OTHER = 'wxfedcba9876543210'
SECRET = 'private opaque credential 0123456789'
PASSWORD = 'Isolated-owner-password-994!'


@override_settings(WECHAT_MINI_APP_ID=APP, WECHAT_MINI_APP_SECRET='environment-secret')
class CredentialApiBase(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.key = Path(self.temporary.name) / 'key'
        self.key.write_bytes(Fernet.generate_key())
        self.override = override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=str(self.key))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.owner = AdminAccount.objects.create_user('integration-owner', PASSWORD, kind='OWNER')
        self.client.post('/api/v1/admin/auth/login', {'loginName': self.owner.login_name,
                         'password': PASSWORD}, content_type='application/json')

    def confirmation(self, action='update', revision=0, client=None, **extra):
        result = (client or self.client).post('/api/v1/admin/auth/confirm', {
            'action': 'wechat.integration.' + action, 'objectId': 'wechat-mini-program',
            'revision': revision, 'password': PASSWORD, **extra}, content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()['data']['confirmationToken']

    def save(self, revision=0, app_id=APP, secret=SECRET, token=None):
        body = {'expectedRevision': revision, 'appId': app_id, 'secretAction': 'REPLACE', 'appSecret': secret}
        return self.client.put(BASE, body, content_type='application/json',
                               HTTP_X_ACTION_CONFIRMATION=token or self.confirmation(revision=revision))

    def member(self, app_id=APP):
        return Member.objects.create(wechat_app_id=app_id, wechat_openid='existing-'+app_id,
                                     grade=MemberGrade.objects.get(code='normal'))


class CredentialApiTests(CredentialApiBase):
    def test_environment_get_is_private_masked_and_legacy_compatible(self):
        result = self.client.get(BASE)
        self.assertEqual(result.status_code, 200)
        data = result.json()['data']
        self.assertEqual((data['revision'], data['source'], data['appId']), (0, 'ENV', APP))
        self.assertTrue(data['secretConfigured'])
        self.assertTrue(data['keyAvailable'])
        self.assertEqual(data['identityBinding'], {'status': 'EMPTY', 'appId': None})
        self.assertEqual(data['notificationsStatus'], 'NOT_VERIFIED')
        self.assertIsNone(data['lastCheck'])
        self.assertNotIn('environment-secret', result.content.decode())
        self.assertIn('no-store', result['Cache-Control'])
        with override_settings(WECHAT_MINI_APP_ID='old-test-app'):
            self.assertEqual(self.client.get(BASE).json()['data']['appId'], 'old-test-app')

    def test_saved_secret_is_encrypted_and_never_in_response_or_audit(self):
        from wechat_integration.models import MiniProgramIntegration
        from wechat_integration.credentials import effective_credentials
        result = self.save()
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual((result.json()['data']['revision'], result.json()['data']['source']), (1, 'MANAGED'))
        row = MiniProgramIntegration.objects.get(pk=1)
        self.assertNotIn(SECRET, row.encrypted_payload)
        self.assertNotIn(APP, row.encrypted_payload)
        snapshot = effective_credentials()
        self.assertEqual((snapshot.app_id, snapshot.secret), (APP, SECRET))
        self.assertNotIn(SECRET, repr(snapshot))
        logs = json.dumps(list(AuditLog.objects.values('before', 'after')))
        for value in [SECRET, row.encrypted_payload]:
            self.assertNotIn(value, logs + result.content.decode())
        self.assertNotIn('appSecret', result.content.decode())

    def test_keep_preserves_secret_and_rejects_ambiguous_or_invalid_inputs(self):
        self.assertEqual(self.save().status_code, 200)
        token = self.confirmation(revision=1)
        kept = self.client.put(BASE, {'expectedRevision': 1, 'appId': APP, 'secretAction': 'KEEP'},
                               content_type='application/json', HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(kept.status_code, 200, kept.content)
        from wechat_integration.credentials import effective_credentials
        self.assertEqual(effective_credentials().secret, SECRET)
        for patch_body in [dict(appSecret=''), dict(appId=OTHER), dict(secretAction='unknown'),
                           dict(expectedRevision=True), dict(extra='ignored')]:
            body = {'expectedRevision': 2, 'appId': APP, 'secretAction': 'KEEP', **patch_body}
            result = self.client.put(BASE, body, content_type='application/json')
            self.assertEqual(result.status_code, 400, result.content)

    def test_confirmation_revision_and_session_binding(self):
        body = {'expectedRevision': 0, 'appId': APP, 'secretAction': 'REPLACE', 'appSecret': SECRET}
        self.assertEqual(self.client.put(BASE, body, content_type='application/json').status_code, 403)
        wrong = self.confirmation(action='test')
        self.assertEqual(self.save(token=wrong).status_code, 403)
        valid = self.confirmation()
        self.assertEqual(self.save(token=valid).status_code, 200)
        self.assertEqual(self.save(revision=1, token=valid).status_code, 403)
        stale = self.save(revision=0)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['error']['code'], 'REVISION_CONFLICT')

    def test_member_identity_anchor_allows_repair_but_not_reassignment(self):
        self.member(APP)
        self.assertEqual(self.save(app_id=OTHER).json()['error']['code'], 'APP_ID_LOCKED')
        with override_settings(WECHAT_MINI_APP_ID=OTHER):
            data = self.client.get(BASE).json()['data']
            self.assertEqual(data['identityBinding'], {'status': 'BOUND', 'appId': APP})
            repaired = self.save(app_id=APP)
            self.assertEqual(repaired.status_code, 200, repaired.content)
        self.member(OTHER)
        self.assertEqual(self.client.get(BASE).json()['data']['identityBinding']['status'], 'MULTIPLE')
        self.assertEqual(self.save(revision=1, app_id=OTHER).status_code, 409)
        self.assertEqual(self.save(revision=1, app_id=APP).status_code, 200)

    def test_missing_key_and_corrupt_managed_credentials_do_not_fall_back(self):
        from wechat_integration.models import MiniProgramIntegration
        from wechat_integration.credentials import CredentialsUnavailable, effective_credentials
        with override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=''):
            self.assertFalse(self.client.get(BASE).json()['data']['keyAvailable'])
            self.assertEqual(self.save().status_code, 503)
        self.assertEqual(self.save().status_code, 200)
        self.key.unlink()
        self.assertEqual(self.client.get(BASE).status_code, 503)
        with self.assertRaises(CredentialsUnavailable):
            effective_credentials()
        self.key.write_bytes(Fernet.generate_key())
        MiniProgramIntegration.objects.filter(pk=1).update(encrypted_payload='damaged')
        self.assertEqual(self.client.get(BASE).status_code, 503)

    def test_probe_uses_stored_snapshot_without_creating_member_or_session(self):
        from wechat_integration.probe import ProbeResult
        with patch('wechat_integration.views.check_credentials', return_value=ProbeResult('SUCCESS', 'OK')) as check:
            result = self.client.post(BASE+'/test', {'expectedRevision': 0}, content_type='application/json',
                HTTP_X_ACTION_CONFIRMATION=self.confirmation(action='test'))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(check.call_args.args[0].secret, 'environment-secret')
        self.assertEqual(result.json()['data']['lastCheck']['status'], 'SUCCESS')
        self.assertEqual(result.json()['data']['revision'], 0)
        for model in [Member, MemberSession, WechatCodeUse]:
            self.assertEqual(model.objects.count(), 0)
        self.assertEqual(self.save().json()['data']['lastCheck'], None)

    def test_probe_discards_stale_result_and_rechecks_disabled_actor(self):
        from wechat_integration.models import MiniProgramIntegration
        from wechat_integration.probe import ProbeResult
        def change(_snapshot):
            MiniProgramIntegration.objects.filter(pk=1).update(revision=1)
            return ProbeResult('SUCCESS', 'OK')
        with patch('wechat_integration.views.check_credentials', side_effect=change):
            result = self.client.post(BASE+'/test', {'expectedRevision': 0}, content_type='application/json',
                HTTP_X_ACTION_CONFIRMATION=self.confirmation(action='test'))
        self.assertEqual(result.status_code, 409)
        self.assertIsNone(MiniProgramIntegration.objects.get(pk=1).last_check_at)
        def disable(_snapshot):
            AdminAccount.objects.filter(pk=self.owner.pk).update(enabled=False)
            return ProbeResult('SUCCESS', 'OK')
        with patch('wechat_integration.views.check_credentials', side_effect=disable):
            result = self.client.post(BASE+'/test', {'expectedRevision': 1}, content_type='application/json',
                HTTP_X_ACTION_CONFIRMATION=self.confirmation(action='test', revision=1))
        self.assertIn(result.status_code, [401, 403])
        self.assertIsNone(MiniProgramIntegration.objects.get(pk=1).last_check_at)

    def test_unauthorized_csrf_methods_and_request_id(self):
        self.assertEqual(Client().get(BASE).status_code, 401)
        staff = AdminAccount.objects.create_user('no-permission', PASSWORD)
        other = Client()
        other.post('/api/v1/admin/auth/login', {'loginName': staff.login_name, 'password': PASSWORD},
                   content_type='application/json')
        self.assertEqual(other.get(BASE).status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.put(BASE, '{}', content_type='application/json').status_code, 403)
        wrong = self.client.delete(BASE)
        self.assertEqual(wrong.status_code, 405)
        self.assertEqual(wrong['Allow'], 'GET, PUT')
        self.assertIn('no-store', wrong['Cache-Control'])
        self.assertTrue(wrong.json()['requestId'])
