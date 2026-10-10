import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet
from django.test import Client, TestCase, override_settings
from accounts.models import AdminAccount, AuditLog


class MapConfigurationTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key_path = Path(self.directory.name) / 'cipher-key'
        self.key_path.write_bytes(Fernet.generate_key())
        self.settings = override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=str(self.key_path), AMAP_WEB_SERVICE_KEY='env-test-key')
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.owner = AdminAccount.objects.create_user('map-owner', 'Map safe password 2026', kind='OWNER')
        self.client = Client(enforce_csrf_checks=True)
        self.client.get('/api/v1/admin/auth/csrf')
        self.post('/api/v1/admin/auth/login', {'loginName':'map-owner','password':'Map safe password 2026'})

    def post(self, path, body, **headers):
        return self.client.post(path, data=json.dumps(body), content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value, **headers)

    def payload(self, revision=0):
        return {'expectedRevision':revision,'webServiceKey':'map-service-test-secret','jsApiKey':'map-js-test-secret','jsSecurityCode':'map-security-test-secret'}

    def confirm(self, revision=0):
        result=self.post('/api/v1/admin/auth/confirm',{'action':'stores.map.configure','objectId':'amap','revision':revision,'password':'Map safe password 2026'})
        self.assertEqual(result.status_code,200,result.content)
        return result.json()['data']['confirmationToken']

    def put(self, body, token=''):
        return self.client.put('/api/v1/admin/stores/map-settings',data=json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,HTTP_X_ACTION_CONFIRMATION=token)

    def test_masked_environment_and_encrypted_managed_persistence(self):
        from stores.models import StoreMapConfiguration
        meta=self.client.get('/api/v1/admin/stores/map-settings').json()['data']
        self.assertEqual(meta['revision'],0)
        self.assertEqual(meta['source'],'ENV')
        self.assertEqual(meta['webServiceKey'],{'configured':True,'tail':'-key'})
        result=self.put(self.payload(),self.confirm())
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['revision'],1)
        row=StoreMapConfiguration.objects.get(pk=1)
        for secret in self.payload().values():
            if isinstance(secret,str):
                self.assertNotIn(secret,row.encrypted_payload)
                self.assertNotIn(secret,result.content.decode())
                self.assertNotIn(secret,str(list(AuditLog.objects.values('before','after'))))
        from stores.map_configuration import effective_configuration
        self.assertEqual(effective_configuration()['webServiceKey'],'map-service-test-secret')
        result=self.put({'expectedRevision':1,'webServiceKey':'','jsApiKey':'','jsSecurityCode':''},self.confirm(1))
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(effective_configuration()['jsApiKey'],'map-js-test-secret')

    def test_auth_csrf_confirmation_revision_and_validation(self):
        self.assertEqual(Client().get('/api/v1/admin/stores/map-settings').status_code,401)
        self.assertEqual(self.client.put('/api/v1/admin/stores/map-settings',data='{}',content_type='application/json').status_code,403)
        self.assertEqual(self.put(self.payload()).status_code,403)
        token=self.confirm()
        self.assertEqual(self.put(self.payload(),token).status_code,200)
        self.assertEqual(self.put(self.payload(1),token).status_code,403)
        self.assertEqual(self.put(self.payload(),self.confirm()).status_code,409)
        self.assertEqual(self.put({**self.payload(1),'unexpected':'secret'},self.confirm(1)).status_code,400)
        self.assertEqual(self.put({**self.payload(1),'jsApiKey':None},self.confirm(1)).status_code,400)

    def test_unavailable_key_and_corrupt_managed_do_not_fallback(self):
        from stores.models import StoreMapConfiguration
        with override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=''):
            self.assertFalse(self.client.get('/api/v1/admin/stores/map-settings').json()['data']['encryptionReady'])
            self.assertEqual(self.put(self.payload(),self.confirm()).status_code,503)
        self.assertEqual(self.put(self.payload(),self.confirm()).status_code,200)
        StoreMapConfiguration.objects.filter(pk=1).update(encrypted_payload='corrupt')
        meta=self.client.get('/api/v1/admin/stores/map-settings').json()['data']
        self.assertFalse(meta['available'])
        with patch('stores.map_views.urlopen') as upstream:
            self.assertEqual(self.client.get('/api/v1/admin/stores/map-search?q=人民路').status_code,503)
            upstream.assert_not_called()
        # An operator can repair corruption by replacing all credentials.
        self.assertEqual(self.put(self.payload(1),self.confirm(1)).status_code,200)

    def test_managed_search_uses_secret_without_exposing_it(self):
        self.assertEqual(self.put(self.payload(),self.confirm()).status_code,200)
        fake=MagicMock()
        fake.__enter__.return_value.read.return_value=b'{"status":"1","pois":[]}'
        with patch('stores.map_views.urlopen',return_value=fake) as upstream:
            response=self.client.get('/api/v1/admin/stores/map-search?q=人民路')
            self.assertEqual(response.status_code,200)
            self.assertIn('map-service-test-secret',upstream.call_args.args[0])
            self.assertNotIn('map-service-test-secret',response.content.decode())

    def test_invalid_pair_unknown_method_permissions_and_rate_limit(self):
        from stores.models import StoreRequestQuota
        self.assertEqual(self.client.post('/api/v1/admin/stores/map-settings',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value).status_code,405)
        staff=AdminAccount.objects.create_user('map-staff','Map safe password 2026',kind='STAFF')
        self.post('/api/v1/admin/auth/login',{'loginName':'map-staff','password':'Map safe password 2026'})
        self.assertEqual(self.client.get('/api/v1/admin/stores/map-settings').status_code,403)
        self.post('/api/v1/admin/auth/login',{'loginName':'map-owner','password':'Map safe password 2026'})
        for bad in ({'jsApiKey':'key','jsSecurityCode':''},{'webServiceKey':'bad key'},{'expectedRevision':True}):
            self.assertEqual(self.put({**self.payload(),**bad},self.confirm()).status_code,400)
        self.client.get('/api/v1/admin/stores/map-settings')
        StoreRequestQuota.objects.filter(scope='map_settings').update(count=120)
        self.assertEqual(self.client.get('/api/v1/admin/stores/map-settings').status_code,429)

    def test_credential_purpose_and_type_fail_closed(self):
        from stores.models import StoreMapConfiguration
        from wechat_open_platform.crypto import seal
        from stores.map_configuration import configuration_data
        for encrypted in (seal('different-purpose',dict.fromkeys(('webServiceKey','jsApiKey','jsSecurityCode'),'test')),seal('stores-amap-credentials',{'webServiceKey':False})):
            StoreMapConfiguration.objects.filter(pk=1).update(managed=True,encrypted_payload=encrypted)
            self.assertFalse(configuration_data()['available'])
            self.assertEqual(self.put({**self.payload(),'webServiceKey':''},self.confirm()).status_code,503)
        self.assertEqual(self.put({**self.payload(),'jsApiKey':'','jsSecurityCode':''},self.confirm()).status_code,200)
