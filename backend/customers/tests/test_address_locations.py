import json
from io import BytesIO
from unittest.mock import patch
from django.test import TestCase, override_settings
from catalog.models import MemberGrade
from customers.models import Member, MemberSession, CustomerAddress

@override_settings(WECHAT_MINI_APP_ID='wx-app', WECHAT_MINI_APP_SECRET='test', AMAP_WEB_SERVICE_KEY='test-key')
class AddressLocationTests(TestCase):
    def setUp(self):
        self.member=Member.objects.create(wechat_app_id='wx-app',wechat_openid='address-map',grade=MemberGrade.objects.get(code='normal'))
        self.token=MemberSession.issue(self.member)[0]
        self.headers={'HTTP_AUTHORIZATION':'Bearer '+self.token}
        self.base={'recipientName':'测试','phone':'13800001234','province':'广东省','city':'深圳市','district':'盐田区','detail':'悦千山雅园 1栋101','isDefault':False}

    def test_structured_address_and_native_coordinate_precision(self):
        body={**self.base,'poiName':'悦千山雅园','locationAddress':'北山道88号','doorDetail':'1栋101','latitude':22.6123456789,'longitude':114.2612345678}
        result=self.client.post('/api/v1/app/addresses',json.dumps(body),content_type='application/json',**self.headers)
        self.assertEqual(result.status_code,201,result.content)
        saved=result.json()['data']; self.assertEqual(saved['poiName'],'悦千山雅园');self.assertEqual(saved['latitude'],'22.612346')
        self.assertEqual(saved['doorDetail'],'1栋101')
        self.assertIn('悦千山雅园',saved['detail'])

    def test_partial_location_rejected_and_legacy_supported(self):
        r=self.client.post('/api/v1/app/addresses',json.dumps({**self.base,'poiName':'单独名称'}),content_type='application/json',**self.headers)
        self.assertEqual(r.status_code,400)
        r=self.client.post('/api/v1/app/addresses',json.dumps(self.base),content_type='application/json',**self.headers)
        self.assertEqual(r.status_code,201)

    @patch('customers.location_service.urlopen')
    def test_search_is_authenticated_and_returns_region_without_key(self,upstream):
        self.assertEqual(self.client.get('/api/v1/app/address-locations/search',{'q':'雅园'}).status_code,401)
        raw={'status':'1','pois':[{'name':'悦千山雅园','address':'北山道88号','pname':'广东省','cityname':'深圳市','adname':'盐田区','location':'114.26,22.61'}]}
        upstream.return_value.__enter__.return_value=BytesIO(json.dumps(raw).encode())
        r=self.client.get('/api/v1/app/address-locations/search',{'q':'雅园','city':'深圳市'},**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()['data']['items'][0]['district'],'盐田区')
        self.assertNotIn('test-key',str(r.content))

    @patch('customers.location_service.urlopen')
    def test_upstream_error_safe_and_rate_limited(self,upstream):
        upstream.side_effect=OSError('secret-url')
        r=self.client.get('/api/v1/app/address-locations/search',{'q':'雅园'},**self.headers)
        self.assertEqual(r.status_code,502);self.assertNotIn('secret-url',str(r.content))
        from customers.models import MemberProfileQuota
        MemberProfileQuota.objects.filter(member=self.member,scope='address-map').update(count=60)
        self.assertEqual(self.client.get('/api/v1/app/address-locations/search',{'q':'雅园'},**self.headers).status_code,429)

    def test_eligibility_unlocated_and_ownership(self):
        r=self.client.get('/api/v1/app/addresses',**self.headers)
        self.assertEqual(r.json()['data'],[])
        r=self.client.get('/api/v1/app/address-locations/eligibility',{'storeId':'bad'},**self.headers)
        self.assertEqual(r.status_code,400)

    @patch('customers.location_service.urlopen')
    def test_reverse_returns_selected_coordinate_and_region(self, upstream):
        upstream.return_value.__enter__.return_value = BytesIO(json.dumps({'status':'1','regeocode':{
            'formatted_address':'广东省深圳市盐田区北山道88号','addressComponent':{'province':'广东省','city':'深圳市','district':'盐田区'},
            'pois':[{'name':'悦千山雅园','location':'114.2,22.6'}]}}).encode())
        r=self.client.get('/api/v1/app/address-locations/reverse',{'latitude':'22.6123456789','longitude':'114.2612345678'},**self.headers)
        self.assertEqual(r.status_code,200,r.content)
        self.assertEqual(r.json()['data']['latitude'],'22.612346')
        self.assertEqual(r.json()['data']['poiName'],'广东省深圳市盐田区北山道88号')

    def test_range_is_current_store_and_private_address_list(self):
        from inventory.models import Warehouse
        from stores.models import Store
        store=Store.objects.create(warehouse=Warehouse.objects.create(code='ADDR',name='地址仓'),name='地址仓',
            supported_modes=['DELIVERY'],latitude='22.61',longitude='114.26',delivery_radius_meters=3000,delivery_fee_fen=10)
        for lat,expected in [('22.61','ELIGIBLE'),('23.61','OUT_OF_RANGE'),(None,'UNLOCATED')]:
            args={'storeId':str(store.id)}
            if lat: args.update(latitude=lat,longitude='114.26')
            r=self.client.get('/api/v1/app/address-locations/eligibility',args,**self.headers)
            self.assertEqual(r.status_code,200,r.content); self.assertEqual(r.json()['data']['code'],expected)
        other=Member.objects.create(wechat_app_id='wx-app',wechat_openid='other-address',grade=self.member.grade)
        CustomerAddress.objects.create(member=other,recipient_name='other',phone='13800001234',province='粤',city='深',district='盐',detail='隐私地址')
        owned=CustomerAddress.objects.create(member=self.member,recipient_name='me',phone='13800001234',province='粤',city='深',district='盐',detail='本人地址')
        r=self.client.get('/api/v1/app/addresses',{'storeId':str(store.id)},**self.headers)
        self.assertEqual(len(r.json()['data']),1)
        self.assertEqual(r.json()['data'][0]['id'],str(owned.id))
        self.assertEqual(r.json()['data'][0]['deliveryEligibility']['code'],'UNLOCATED')
        store.accepting_orders=False;store.save()
        r=self.client.get('/api/v1/app/address-locations/eligibility',{'storeId':str(store.id)},**self.headers)
        self.assertEqual(r.json()['data']['code'],'UNAVAILABLE')

    def test_door_edit_preserves_coordinates_and_legacy_location_edit_clears(self):
        body={**self.base,'poiName':'雅园','locationAddress':'北山道88号','doorDetail':'1栋101','latitude':'22.61','longitude':'114.26'}
        r=self.client.post('/api/v1/app/addresses',json.dumps(body),content_type='application/json',**self.headers)
        saved=r.json()['data'];url='/api/v1/app/addresses/'+saved['id']
        r=self.client.put(url,json.dumps({**body,'doorDetail':'1栋102','expectedRevision':saved['revision']}),content_type='application/json',**self.headers)
        self.assertEqual(r.status_code,200,r.content); self.assertEqual(r.json()['data']['latitude'],'22.610000')
        r=self.client.put(url,json.dumps({**self.base,'detail':'另一个小区','latitude':'22.61','longitude':'114.26','expectedRevision':r.json()['data']['revision']}),content_type='application/json',**self.headers)
        self.assertEqual(r.status_code,200,r.content);self.assertIsNone(r.json()['data']['latitude'])
        self.assertEqual(r.json()['data']['poiName'],'')

    def test_coordinate_nonfinite_and_missing_pair_rejected(self):
        for args in ({'latitude':'NaN','longitude':'114'},{'latitude':'91','longitude':'114'},{'latitude':'22'}):
            r=self.client.get('/api/v1/app/address-locations/reverse',args,**self.headers)
            self.assertEqual(r.status_code,400,r.content)

    def test_private_responses_disable_caching_including_errors(self):
        for url,headers in (
            ('/api/v1/app/addresses',self.headers),
            ('/api/v1/app/addresses',{}),
            ('/api/v1/app/address-locations/search?q=x',self.headers),
            ('/api/v1/app/address-locations/search?q=雅园',{}),
            ('/api/v1/app/addresses/00000000-0000-0000-0000-000000000001',self.headers),
        ):
            r=self.client.get(url,**headers)
            self.assertIn('private',r.headers.get('Cache-Control',''))
            self.assertIn('no-store',r.headers.get('Cache-Control',''))
            self.assertEqual({part.strip().lower() for part in r.headers['Vary'].split(',')}, {'authorization','cookie'})
