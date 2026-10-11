"""Warehouse logo ownership and private map runtime boundaries."""
from unittest.mock import patch, MagicMock
from django.test import override_settings
from catalog.models import Asset
from catalog.media import orphan_assets
from stores.models import Store
from stores.service import save_store, store_data
from stores.access import StoreError
from .test_store_api import StoreApiTests


class WarehouseLogoMapTests(StoreApiTests):
    def asset(self, **changes):
        values = dict(kind='IMAGE', content_type='image/png', byte_size=1024, width=400, height=400,
                      sha256='a'*64, original_name='logo.png', stored_name='media/product/logo.png', created_by=self.owner)
        return Asset.objects.create(**{**values, **changes})

    @patch('catalog.media.available_asset', return_value=True)
    def test_logo_bind_project_clear_and_retention(self, available):
        asset = self.asset()
        result = save_store({'revision': self.store.revision, 'logoAssetId': str(asset.id)}, self.store, actor=self.owner)
        self.assertEqual(store_data(result)['logoAssetId'], str(asset.id))
        self.assertEqual(store_data(result)['logoUrl'], f'/api/v1/app/assets/{asset.id}/file')
        self.assertFalse(orphan_assets().filter(id=asset.id).exists())
        self.assertEqual(self.client.delete(f'/api/v1/admin/assets/{asset.id}', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value).status_code, 409)
        result = save_store({'revision': result.revision, 'logoAssetId': None}, result, actor=self.owner)
        self.assertIsNone(result.logo_id)
        self.assertTrue(orphan_assets().filter(id=asset.id).exists())

    @patch('catalog.media.available_asset', return_value=True)
    def test_invalid_logo_geometry_type_and_size_rejected(self, available):
        for values in ({'height': 300}, {'byte_size': 5242881}, {'kind':'GIF', 'content_type':'image/gif'}):
            asset = self.asset(stored_name='media/product/'+str(len(values))+str(values)+'.png', **values)
            with self.assertRaises(StoreError):
                save_store({'revision': self.store.revision, 'logoAssetId': str(asset.id)}, self.store, actor=self.owner)
        self.store.refresh_from_db()
        self.assertIsNone(self.store.logo_id)

    @patch('catalog.media.available_asset', return_value=False)
    def test_missing_logo_rejected(self, available):
        asset = self.asset()
        result = self.send('patch', f'/api/v1/admin/stores/{self.store.id}', {'revision': self.store.revision, 'logoAssetId':str(asset.id)})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json()['error']['code'], 'MEDIA_INVALID')

    @patch('stores.map_views.effective_configuration', return_value={'jsApiKey':'browser-key','jsSecurityCode':'private-code','webServiceKey':'private-web'})
    def test_map_runtime_never_reveals_server_credentials(self, configuration):
        result = self.client.get('/api/v1/admin/stores/map-runtime')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['jsApiKey'], 'browser-key')
        self.assertNotIn('private', result.content.decode())
        self.assertIn('no-store', result['Cache-Control'])
        self.client.logout()
        self.assertEqual(self.client.get('/api/v1/admin/stores/map-runtime').status_code, 401)

    @patch('stores.map_views.reverse', return_value={'poiName':'Garden','locationAddress':'Road 1','city':'深圳','latitude':'22.5','longitude':'114.1'})
    def test_map_reverse_validates_coordinates(self, reverse):
        result = self.client.get('/api/v1/admin/stores/map-reverse', {'latitude':'22.5','longitude':'114.1'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['name'], 'Garden')
        self.assertIn('no-store',result['Cache-Control'])
        for data in ({'latitude':'NaN','longitude':'114.1'}, {'latitude':'22.5'}, {'latitude':'91','longitude':'0'}):
            self.assertEqual(self.client.get('/api/v1/admin/stores/map-reverse', data).status_code,400)

    def test_proxy_rejects_arbitrary_hosts_and_unknown_paths(self):
        self.assertEqual(self.client.get('/api/v1/admin/stores/amap-proxy/_AMapService/https://evil.test').status_code,404)
        self.assertEqual(self.client.get('/api/v1/admin/stores/amap-proxy/_AMapService/v4/map/styles?a=1&a=2').status_code,400)

    @patch('catalog.media.available_asset', return_value=True)
    def test_store_logo_reference_and_visibility(self, available):
        from catalog.asset_library import references_page
        from django.test import RequestFactory
        asset = self.asset()
        save_store({'revision': self.store.revision, 'logoAssetId':str(asset.id)}, self.store, actor=self.owner)
        references = references_page(RequestFactory().get('/'), asset.id, {'stores.read'})
        self.assertEqual(references['items'][0]['domain'], 'STORE')
        self.assertEqual(references['items'][0]['label'], self.store.name)
        denied = references_page(RequestFactory().get('/'), asset.id, set())
        self.assertIsNone(denied['items'][0]['objectId'])
        with patch('catalog.media_views.file_response') as serve:
            serve.return_value = self.client.get('/non-existent')
            self.client.get(f'/api/v1/app/assets/{asset.id}/file')
            serve.assert_called_once()
            Store.objects.filter(pk=self.store.id).update(enabled=False)
            serve.reset_mock()
            self.assertEqual(self.client.get(f'/api/v1/app/assets/{asset.id}/file').status_code,404)
            serve.assert_not_called()

    @patch('stores.map_views.effective_configuration', return_value={'jsApiKey':'browser-key','jsSecurityCode':'private-code','webServiceKey':'private-web'})
    @patch('urllib.request.build_opener')
    def test_proxy_credentials_fixed_endpoint_no_redirect_and_cache(self, opener, configuration):
        upstream = MagicMock()
        upstream.__enter__.return_value = upstream
        upstream.read.return_value = b'{"styles":[]}'
        upstream.headers = {'Content-Type':'application/json'}
        opener.return_value.open.return_value = upstream
        result = self.client.get('/api/v1/admin/stores/amap-proxy/_AMapService/v4/map/styles', {'key':'attacker','jscode':'attacker'})
        self.assertEqual(result.status_code,200)
        self.assertIn('no-store',result['Cache-Control'])
        url = opener.return_value.open.call_args.args[0]
        self.assertTrue(url.startswith('https://webapi.amap.com/v4/map/styles?'))
        self.assertIn('jscode=private-code',url)
        self.assertNotIn('attacker',url)
        self.assertNotIn('private-code',result.content.decode())
        self.assertEqual(opener.call_args.args[0].redirect_request(None,None,None,None,None,None),None)

    def test_map_configuration_failures_are_sanitized(self):
        from wechat_integration.credentials import CredentialsUnavailable
        from customers.location_service import LocationError
        with patch('stores.map_views.effective_configuration', side_effect=CredentialsUnavailable()):
            for path in ('map-runtime','amap-proxy/_AMapService/v4/map/styles'):
                self.assertEqual(self.client.get('/api/v1/admin/stores/'+path).status_code,503)
        with patch('stores.map_views.effective_configuration', return_value={'jsApiKey':'','jsSecurityCode':'','webServiceKey':''}):
            for path in ('map-runtime','amap-proxy/_AMapService/v4/map/styles'):
                self.assertEqual(self.client.get('/api/v1/admin/stores/'+path).status_code,503)
        with patch('stores.map_views.reverse', side_effect=LocationError('地图失败')):
            self.assertEqual(self.client.get('/api/v1/admin/stores/map-reverse', {'latitude':'22','longitude':'114'}).status_code,502)

    @patch('stores.map_views.effective_configuration', return_value={'jsApiKey':'browser-key','jsSecurityCode':'private-code','webServiceKey':'private-web'})
    @patch('urllib.request.build_opener')
    def test_proxy_rejects_script_oversize_and_network_failures(self, opener, configuration):
        upstream = MagicMock()
        upstream.__enter__.return_value = upstream
        opener.return_value.open.return_value = upstream
        upstream.headers = {'Content-Type':'application/javascript'}
        upstream.read.return_value = b'evil()'
        path = '/api/v1/admin/stores/amap-proxy/_AMapService/v4/map/styles'
        self.assertEqual(self.client.get(path).status_code,502)
        upstream.headers = {'Content-Type':'application/json'}
        upstream.read.return_value = b'a'*1048577
        self.assertEqual(self.client.get(path).status_code,502)
        upstream.read.return_value = b'{"error":"private-code"}'
        self.assertEqual(self.client.get(path).status_code,502)
        opener.return_value.open.side_effect = OSError('private-code')
        failed = self.client.get(path)
        self.assertEqual(failed.status_code,502)
        self.assertNotIn('private-code',failed.content.decode())
        self.assertEqual(self.client.get(path, {'q':'a'*4100}).status_code,400)

    @patch('catalog.media.available_asset', return_value=True)
    def test_logo_binding_denies_foreign_asset_and_invalid_uuid(self, available):
        from accounts.models import AdminAccount
        actor = AdminAccount.objects.create_user('warehouse-restricted', 'Safe phrase 2026 test', kind='STAFF')
        asset = self.asset()
        with self.assertRaises(StoreError) as denied:
            save_store({'revision': self.store.revision, 'logoAssetId':str(asset.id)}, self.store, actor=actor)
        self.assertEqual(denied.exception.status,403)
        for identifier in ('invalid', 12, ''):
            with self.assertRaises(StoreError):
                save_store({'revision':self.store.revision, 'logoAssetId':identifier}, self.store, actor=self.owner)
