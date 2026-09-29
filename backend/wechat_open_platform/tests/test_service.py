import base64
import hashlib
import json
import os
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.test import TestCase, override_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import AdminAccount
from wechat_open_platform.crypto import InvalidCallback, decode_callback
from wechat_open_platform.models import AuthorizerGrant, ComponentConfig
from wechat_open_platform.service import (PlatformStateError, authorization_status, begin_authorization,
                                          component_settings, configure_component, finish_authorization,
                                          get_audit_status, get_category, get_latest_auditstatus,
                                          receive_encrypted_event, release_approved, submit_audit)

COMPONENT = 'wx1111111111111111'
DEVELOPER = 'wx2222222222222222'
TARGET = 'wx3333333333333333'
KEY = 'abcdefghijklmnopqrstuvwxyzABCDEFGH123456789'


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.audit_status = 0
        self.latest_auditid = 77
        self.latest_user_version = 'source-test'
        self.unknown = False

    def __call__(self, method, path, *, access_token='', payload=None):
        self.calls.append((method, path, access_token, payload))
        if self.unknown:
            raise TimeoutError('unavailable')
        responses = {
            '/cgi-bin/component/api_component_token': {'component_access_token': 'component-token', 'expires_in': 7200},
            '/cgi-bin/component/api_create_preauthcode': {'pre_auth_code': 'preauth', 'expires_in': 600},
            '/cgi-bin/component/api_query_auth': {'authorization_info': {
                'authorizer_appid': TARGET, 'authorizer_refresh_token': 'refresh-secret',
                'authorizer_access_token': 'authorizer-token', 'expires_in': 7200,
                'func_info': [{'funcscope_category': {'id': 18}}]}},
            '/cgi-bin/component/api_authorizer_token': {'authorizer_access_token': 'authorizer-token',
                                                         'authorizer_refresh_token': 'refresh-secret',
                                                         'expires_in': 7200},
            '/cgi-bin/component/api_get_authorizer_info': {'authorizer_info': {
                'MiniProgramInfo': {}, 'account_status': 1}, 'authorization_info': {
                'authorizer_appid': TARGET,
                'func_info': [{'funcscope_category': {'id': 18}}]}},
            '/wxa/submit_audit': {'errcode': 0, 'auditid': 77},
            '/wxa/get_category': {'errcode': 0, 'category_list': [{
                'first_class': '工具', 'second_class': '效率', 'first_id': 1, 'second_id': 2}]},
            '/wxa/get_auditstatus': {'errcode': 0, 'status': self.audit_status},
            '/wxa/get_latest_auditstatus': {'errcode': 0, 'auditid': self.latest_auditid,
                                            'status': self.audit_status,
                                            **({'user_version': self.latest_user_version}
                                               if self.latest_user_version is not None else {})},
            '/wxa/release': {'errcode': 0},
        }
        return responses[path]


class PlatformServiceTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        key_file = Path(temporary.name) / 'credential.key'
        key_file.write_bytes(Fernet.generate_key())
        setting = override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=str(key_file))
        setting.enable()
        self.addCleanup(setting.disable)
        self.fake = FakeTransport()
        p = patch('wechat_open_platform.client._transport', self.fake)
        p.start()
        self.addCleanup(p.stop)
        configure_component(component_app_id=COMPONENT, developer_app_id=DEVELOPER,
                            redirect_uri='https://admin.example.test/api/v1/wechat/open-platform/authorization-callback',
                            component_app_secret='component-secret', message_token='message-token',
                            encoding_aes_key=KEY, expected_revision=0)
        row = ComponentConfig.objects.get(pk=1)
        from wechat_open_platform.crypto import seal
        row.encrypted_ticket = seal('component-ticket', 'ticket-value')
        row.save(update_fields=['encrypted_ticket'])

    def test_authorization_is_blocked_until_real_target_grant_and_scope_probe(self):
        self.assertEqual(authorization_status(TARGET)['status'], 'BLOCKED')
        start = begin_authorization(TARGET)
        self.assertIn('biz_appid=' + TARGET, start['url'])
        self.assertIn('category_id_list=18', start['url'])
        state = start['state']
        self.assertEqual(finish_authorization(state, 'auth-code')['authorizerAppId'], TARGET)
        self.assertEqual(authorization_status(TARGET)['status'], 'PASS')
        self.assertNotIn('refresh-secret', component_settings().__repr__())
        self.assertNotIn('refresh-secret', AuthorizerGrant.objects.get(authorizer_app_id=TARGET).encrypted_refresh_token)
        with self.assertRaises(PlatformStateError):
            finish_authorization(state, 'auth-code')
        self.fake.unknown = True
        self.assertEqual(authorization_status(TARGET)['status'], 'UNVERIFIED')

    def test_release_preflight_rejects_corrupt_component_ciphertext(self):
        ComponentConfig.objects.filter(pk=1).update(encrypted_credentials='corrupt')
        with self.assertRaises(CommandError):
            call_command('check_wechat_credentials')

    def test_submit_audit_and_publish_exact_latest_audit_only(self):
        start = begin_authorization(TARGET)
        finish_authorization(start['state'], 'auth-code')
        auditid = submit_audit(TARGET, item_list=[{'first_class': '工具', 'second_class': '效率',
                                                   'first_id': 1, 'second_id': 2}], version_desc='test')
        self.assertEqual(auditid, 77)
        self.assertEqual(get_audit_status(TARGET, 77)['status'], 0)
        self.assertEqual(get_latest_auditstatus(TARGET)['auditid'], 77)
        self.assertEqual(get_category(TARGET)[0]['first_class'], '工具')
        self.fake.latest_auditid = 78
        with self.assertRaises(PlatformStateError):
            release_approved(TARGET, auditid=77, user_version='source-test')
        self.assertFalse(any(path == '/wxa/release' for _, path, _, _ in self.fake.calls))
        self.fake.latest_auditid = 77
        release_approved(TARGET, auditid=77, user_version='source-test')
        self.assertEqual([path for _, path, _, body in self.fake.calls if path == '/wxa/release'],
                         ['/wxa/release'])

    def test_release_accepts_latest_audit_without_optional_version_field(self):
        start = begin_authorization(TARGET)
        finish_authorization(start['state'], 'auth-code')
        self.fake.latest_auditid = '77'
        self.fake.latest_user_version = None
        self.assertEqual(get_latest_auditstatus(TARGET)['auditid'], 77)
        release_approved(TARGET, auditid=77, user_version='source-test')
        self.assertTrue(any(path == '/wxa/release' for _, path, _, _ in self.fake.calls))
        self.fake.latest_user_version = 'different-version'
        with self.assertRaises(PlatformStateError):
            release_approved(TARGET, auditid=77, user_version='source-test')

    def test_callback_signature_aes_and_component_binding(self):
        plain = f'<xml><AppId>{COMPONENT}</AppId><InfoType>component_verify_ticket</InfoType>' \
                '<ComponentVerifyTicket>ticket</ComponentVerifyTicket></xml>'
        clear = os.urandom(16) + struct.pack('!I', len(plain.encode())) + plain.encode() + COMPONENT.encode()
        size = 32 - (len(clear) % 32)
        aes_key = base64.b64decode(KEY + '=')
        encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16])).encryptor()
        encrypted = base64.b64encode(encryptor.update(clear + bytes([size]) * size) + encryptor.finalize()).decode()
        raw = ('<xml><Encrypt><![CDATA[' + encrypted + ']]></Encrypt></xml>').encode()
        timestamp, nonce = '123', '456'
        digest = hashlib.sha1(''.join(sorted(['message-token', timestamp, nonce, encrypted])).encode()).hexdigest()
        parsed = decode_callback(raw, signature=digest, timestamp=timestamp, nonce=nonce,
                                 token='message-token', encoding_key=KEY, component_app_id=COMPONENT)
        self.assertEqual(parsed['ComponentVerifyTicket'], 'ticket')
        with self.assertRaises(InvalidCallback):
            decode_callback(raw, signature='0' * 40, timestamp=timestamp, nonce=nonce,
                            token='message-token', encoding_key=KEY, component_app_id=COMPONENT)

    def _event(self, body):
        plain = '<xml><AppId>' + COMPONENT + '</AppId><CreateTime>' + str(int(timezone.now().timestamp())) + \
                '</CreateTime>' + body + '</xml>'
        clear = os.urandom(16) + struct.pack('!I', len(plain.encode())) + plain.encode() + COMPONENT.encode()
        size = 32 - (len(clear) % 32)
        aes_key = base64.b64decode(KEY + '=')
        encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16])).encryptor()
        encrypted = base64.b64encode(encryptor.update(clear + bytes([size]) * size) + encryptor.finalize()).decode()
        raw = ('<xml><Encrypt><![CDATA[' + encrypted + ']]></Encrypt></xml>').encode()
        timestamp = str(int(timezone.now().timestamp()))
        signature = hashlib.sha1(''.join(sorted(['message-token', timestamp, '456', encrypted])).encode()).hexdigest()
        return raw, {'signature': signature, 'timestamp': timestamp, 'nonce': '456'}

    def test_live_permission_missing_and_revocation_are_not_pass(self):
        start = begin_authorization(TARGET)
        finish_authorization(start['state'], 'auth-code')
        def no_scope(method, path, *, access_token='', payload=None):
            result = self.fake(method, path, access_token=access_token, payload=payload)
            if path == '/cgi-bin/component/api_get_authorizer_info':
                result['authorization_info']['func_info'] = []
            return result
        with patch('wechat_open_platform.client._transport', no_scope):
            status = authorization_status(TARGET)
        self.assertEqual((status['status'], status['code']), ('BLOCKED', 'CODE_SCOPE_MISSING'))
        raw, meta = self._event('<InfoType>unauthorized</InfoType><AuthorizerAppid>' + TARGET + '</AuthorizerAppid>')
        self.assertEqual(receive_encrypted_event(raw, **meta), 'unauthorized')
        self.assertEqual(authorization_status(TARGET)['status'], 'BLOCKED')
        self.assertEqual(AuthorizerGrant.objects.get(authorizer_app_id=TARGET).encrypted_refresh_token, '')

    def test_admin_views_hide_secrets_and_callback_consumes_state(self):
        password = 'Platform-test-password-9!'
        AdminAccount.objects.create_user('platform-owner', password, kind='OWNER')
        login = self.client.post('/api/v1/admin/auth/login', {
            'loginName': 'platform-owner', 'password': password}, content_type='application/json')
        self.assertEqual(login.status_code, 200)
        base = '/api/v1/admin/integrations/wechat-open-platform'
        read = self.client.get(base)
        self.assertEqual(read.status_code, 200)
        self.assertNotIn('component-secret', read.content.decode())
        self.assertNotIn('message-token', read.content.decode())
        self.assertNotIn(KEY, read.content.decode())
        update = {'componentAppId': COMPONENT, 'developerAppId': DEVELOPER,
                  'redirectUri': 'https://admin.example.test/api/v1/wechat/open-platform/authorization-callback',
                  'componentAppSecret': 'rotated-secret', 'messageToken': 'message-token',
                  'encodingAesKey': KEY, 'expectedRevision': 1}
        denied = self.client.put(base, update, content_type='application/json')
        self.assertEqual(denied.status_code, 403)
        confirmed = self.client.post('/api/v1/admin/auth/confirm', {
            'action': 'wechat.integration.update', 'objectId': 'wechat-open-platform',
            'revision': 1, 'password': password}, content_type='application/json')
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        saved = self.client.put(base, update, content_type='application/json',
                                HTTP_X_ACTION_CONFIRMATION=confirmed.json()['data']['confirmationToken'])
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertNotIn('rotated-secret', saved.content.decode())
        started = self.client.post(base + '/authorize', {'targetAppId': TARGET},
                                   content_type='application/json')
        self.assertEqual(started.status_code, 200, started.content)
        url = started.json()['data']['url']
        from urllib.parse import parse_qs, urlsplit
        callback = parse_qs(urlsplit(parse_qs(urlsplit(url).query)['redirect_uri'][0]).query)
        state = callback['state'][0]
        finished = self.client.get('/api/v1/wechat/open-platform/authorization-callback', {
            'state': state, 'auth_code': 'auth-code', 'expires_in': '600'})
        self.assertEqual(finished.status_code, 200, finished.content)
        repeated = self.client.get('/api/v1/wechat/open-platform/authorization-callback', {
            'state': state, 'auth_code': 'auth-code', 'expires_in': '600'})
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(self.client.get(base + '/authorization/' + TARGET).json()['data']['status'], 'PASS')
