import json
from unittest.mock import patch,MagicMock
from django.test import TestCase,Client,override_settings
from accounts.models import AdminAccount
from .test_store_access import StoreAccessTests
from stores.models import StoreStaff,StoreRequestQuota
from stores.service import store_data,save_store,update_product,distance_meters
from stores.access import StoreError,get_store


class StoreApiTests(StoreAccessTests):
    def test_nearest_match_is_ranked_before_response_limit(self):
        from stores.models import Store
        from inventory.models import Warehouse
        self.store.latitude, self.store.longitude = '22.500000', '114.100000'
        self.store.name = 'ZZZ-nearest'
        self.store.save()
        warehouses = [Warehouse(code=f'NEAR-{index}',name=f'远店{index}') for index in range(205)]
        Warehouse.objects.bulk_create(warehouses)
        Store.objects.bulk_create([Store(warehouse=warehouse,name=f'AAA-{index:03}',latitude='40.000000',
            longitude='116.000000') for index,warehouse in enumerate(warehouses)])
        result = self.client.get('/api/v1/app/stores',{'latitude':'22.5','longitude':'114.1'})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['items'][0]['id'],str(self.store.id))
        self.assertEqual(len(result.json()['data']['items']),200)

    def setUp(self):
        super().setUp()
        self.owner=AdminAccount.objects.create_user('store-owner','Store safe phrase 2026',kind='OWNER')
        self.client=Client(enforce_csrf_checks=True)
        self.client.get('/api/v1/admin/auth/csrf')
        self.send('post','/api/v1/admin/auth/login',{'loginName':'store-owner','password':'Store safe phrase 2026'})

    def send(self,verb,path,body=None):
        return getattr(self.client,verb)(path,data=json.dumps(body or {}),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def test_admin_create_edit_and_staff_assignment(self):
        result=self.send('post','/api/v1/admin/stores',{'name':'新增前置仓','contactName':'张先生','contactPhone':'13800000000','address':'人民路','latitude':22.5,'longitude':114.1,'supportedModes':['PICKUP']})
        self.assertEqual(result.status_code,201,result.content)
        data=result.json()['data']
        result=self.send('patch','/api/v1/admin/stores/'+data['id'],{'revision':1,'acceptingOrders':False})
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.send('patch','/api/v1/admin/stores/'+data['id'],{'revision':1,'name':'stale'}).status_code,409)
        result=self.send('post',f'/api/v1/admin/stores/{self.store.id}/staff',{'memberId':str(self.member.id),'permissions':['orders','accounts'],'enabled':True})
        self.assertEqual(result.status_code,200,result.content)
        result=self.client.get(f'/api/v1/admin/stores/{self.store.id}/staff')
        self.assertEqual(result.json()['data']['items'][0]['permissions'],['accounts','orders'])
        self.assertEqual(self.client.get(f'/api/v1/admin/stores/{self.store.id}').status_code,200)

    def test_staff_assignment_accepts_public_member_number_and_replays(self):
        path = f'/api/v1/admin/stores/{self.store.id}/staff'
        body = {'memberNo': self.member.member_no, 'permissions': ['orders'], 'enabled': True}
        result = self.send('post', path, body)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data']['memberId'], str(self.member.id))
        self.assertEqual(result.json()['data']['memberNo'], self.member.member_no)
        self.assertEqual(self.send('post', path, body).status_code, 200)
        self.assertEqual(StoreStaff.objects.filter(store=self.store, member=self.member).count(), 1)
        self.member.enabled = False
        self.member.save()
        denied = self.send('post', path, body)
        self.assertEqual(denied.status_code, 400)
        self.assertIn('会员不存在或已停用', denied.json()['error']['message'])

    def test_staff_lookup_is_bounded_private_and_requires_management(self):
        path = f'/api/v1/admin/stores/{self.store.id}/staff'
        self.member.nickname = '张小仓'
        self.member.save()
        for search in (self.member.member_no, '张小', str(self.member.id)):
            result = self.client.get(path, {'memberSearch': search})
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(result.json()['data']['members'], [{'id': str(self.member.id), 'memberNo': self.member.member_no, 'name': '张小仓'}])
            self.assertIn('no-store', result['Cache-Control'])
        for search in ('x', 'x' * 101):
            self.assertEqual(self.client.get(path, {'memberSearch': search}).status_code, 400)
        self.assertEqual(self.client.get(path + '?memberSearch=张小&memberSearch=重复').status_code, 400)
        self.assertEqual(self.client.get(path, {'memberSearch': '张小', 'unexpected': 'x'}).status_code, 400)
        self.member.enabled = False
        self.member.save()
        self.assertEqual(self.client.get(path, {'memberSearch': '张小'}).json()['data']['members'], [])
        from accounts.models import PermissionGroup, GroupPermission
        reader = AdminAccount.objects.create_user('store-reader', 'Store safe phrase 2026', kind='STAFF')
        role = PermissionGroup.objects.create(code='store-reader', name='store-reader')
        GroupPermission.objects.create(group=role, code='stores.read')
        reader.permission_groups.add(role)
        self.send('post', '/api/v1/admin/auth/login', {'loginName': reader.login_name, 'password': 'Store safe phrase 2026'})
        self.assertEqual(self.client.get(path, {'memberSearch': self.member.member_no}).status_code, 403)

    def test_staff_invalid_identifier_has_friendly_validation_and_list_survives(self):
        path = f'/api/v1/admin/stores/{self.store.id}/staff'
        for identity in ({'memberId': 'm20260930203055li3'}, {'memberId': []}, {'memberNo': ''}, {'memberNo': 'missing'}, {'memberNo': 'm' * 19}, {'memberId': '00000000-0000-0000-0000-000000000000'}, {'memberNo': self.member.member_no, 'memberId': str(self.member.id)}):
            result = self.send('post', path, {**identity, 'permissions': ['products'], 'enabled': True})
            self.assertEqual(result.status_code, 400, result.content)
        result = self.client.get(path)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data']['items'][0]['memberId'], str(self.member.id))

    def test_csrf_and_admin_auth_required(self):
        self.assertEqual(Client().get('/api/v1/admin/stores').status_code,401)
        self.assertEqual(self.client.post('/api/v1/admin/stores',data='{}',content_type='application/json').status_code,403)
        self.assertEqual(self.send('post','/api/v1/admin/stores',{'name':'incomplete'}).status_code,400)
        self.assertEqual(self.send('post',f'/api/v1/admin/stores/{self.store.id}/staff',{'memberId':str(self.member.id),'permissions':['root'],'enabled':True}).status_code,400)

    def test_public_and_admin_lists_and_empty_financial_account(self):
        result=self.client.get('/api/v1/admin/stores?search=前置仓&page=1&pageSize=1')
        self.assertEqual(len(result.json()['data']['items']),1)
        self.assertEqual(self.client.get('/api/v1/admin/stores?pageSize=1000').status_code,400)
        self.assertEqual(self.client.get('/api/v1/app/stores?latitude=999&longitude=0').status_code,400)
        self.assertEqual(self.client.get('/api/v1/app/stores?latitude=22').status_code,400)
        self.store.latitude=22.5;self.store.longitude=114.1;self.store.save()
        rows=self.client.get('/api/v1/app/stores?latitude=22.5&longitude=114.1').json()['data']['items']
        self.assertEqual(rows[0]['id'],str(self.store.id))
        self.assertEqual(rows[0]['distanceMeters'],0)
        self.assertEqual(self.client.get(f'/api/v1/app/stores/{self.store.id}').status_code,200)
        self.assertEqual(self.client.get('/api/v1/admin/stores/accounts').json()['data']['total'],2)
        account=self.client.get(f'/api/v1/admin/stores/{self.store.id}/account').json()['data']
        self.assertFalse(account['withdrawalReady'])
        self.assertTrue(account['settlementReady'])
        self.assertEqual(account['balance'],{'pendingFen':0,'availableFen':0,'frozenFen':0,'paidFen':0})
        self.assertEqual(account['income'],[])
        self.assertEqual(account['withdrawals'],[])
        from payments.models import StoreWalletEvent,StoreWallet
        self.assertFalse(StoreWallet.objects.filter(store=self.store).exists())
        self.assertFalse(StoreWalletEvent.objects.filter(store=self.store).exists())
        with patch('stores.views.require_member',return_value=(self.member,None)):
            StoreStaff.objects.filter(pk=self.staff.id).update(permissions=['accounts'])
            result=self.send('post',f'/api/v1/app/store-center/stores/{self.store.id}/withdrawals',{})
            self.assertEqual(result.status_code,400)
            import uuid
            result=self.send('post',f'/api/v1/app/store-center/stores/{self.store.id}/withdrawals',{'requestKey':str(uuid.uuid4()),'amountFen':1,'payeeName':'测试姓名','bankName':'测试银行','bankAccount':'6222000012345678'})
            self.assertEqual(result.status_code,409)
            self.assertEqual(result.json()['error']['code'],'INSUFFICIENT_BALANCE')
            account_response=self.client.get(f'/api/v1/app/store-center/stores/{self.store.id}/account')
            self.assertEqual(account_response.status_code,200)
            self.assertIn('no-store',account_response['Cache-Control'])
            self.assertEqual(account_response.json()['data']['balance']['availableFen'],0)

    def test_member_products_up_down_stock_and_cross_store_rejected(self):
        with patch('stores.views.require_member',return_value=(self.member,None)):
            self.assertEqual(self.client.get('/api/v1/app/store-center/stores').status_code,200)
            result=self.client.get(f'/api/v1/app/store-center/stores/{self.store.id}/products')
            row=result.json()['data']['items'][0]
            result=self.send('patch',f'/api/v1/app/store-center/stores/{self.store.id}/products/{self.product.id}',{'revision':row['revision'],'onSale':False,'stock':[{'skuId':str(self.sku.id),'availableBaseUnits':8,'expectedAvailableBaseUnits':0}]})
            self.assertEqual(result.status_code,200,result.content)
            self.assertFalse(result.json()['data']['onSale'])
            self.assertEqual(result.json()['data']['skus'][0]['availableBaseUnits'],8)
            self.assertEqual(self.client.get(f'/api/v1/app/store-center/stores/{self.other.id}/products').status_code,403)
        self.assertEqual(Client().get('/api/v1/app/store-center/stores').status_code,401)

    @override_settings(AMAP_WEB_SERVICE_KEY='')
    def test_map_unconfigured_is_explicit(self):
        self.assertEqual(self.client.get('/api/v1/admin/stores/map-search?q=人民路').status_code,503)

    @override_settings(AMAP_WEB_SERVICE_KEY='testing')
    def test_map_proxy_validates_and_does_not_expose_key(self):
        fake=MagicMock()
        fake.__enter__.return_value.read.return_value=json.dumps({'status':'1','pois':[{'location':'114.1,22.5','name':'前置仓','pname':'广东','cityname':'深圳','address':'路'}]}).encode()
        with patch('stores.map_views.urlopen',return_value=fake):
            result=self.client.get('/api/v1/admin/stores/map-search?q=人民路')
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(result.json()['data']['items'][0]['latitude'],22.5)
        for payload in ([], {'status':'1','pois':[False]}, {'status':'1','pois':None}):
            fake.__enter__.return_value.read.return_value=json.dumps(payload).encode()
            with patch('stores.map_views.urlopen',return_value=fake):
                self.assertEqual(self.client.get('/api/v1/admin/stores/map-search?q=人民路').status_code,502)
        with patch('stores.map_views.urlopen',side_effect=TimeoutError):
            self.assertEqual(self.client.get('/api/v1/admin/stores/map-search?q=人民路').status_code,502)
        self.assertEqual(self.client.get('/api/v1/admin/stores/map-search?q=x').status_code,400)

    def test_quota_validation_and_missing_store(self):
        self.client.get('/api/v1/admin/stores')
        StoreRequestQuota.objects.filter(scope='admin_stores').update(count=120)
        self.assertEqual(self.client.get('/api/v1/admin/stores').status_code,429)
        for body in [{'name':''},{'enabled':1},{'latitude':'NaN'},{'supportedModes':['FAKE']},{'deliveryFeeFen':-1},{'extra':1}]:
            with self.assertRaises(StoreError):save_store({**{'name':'n','contactName':'a','contactPhone':'13800000000','address':'a'},**body})
        with self.assertRaises(StoreError):get_store('invalid')
        with self.assertRaises(StoreError):get_store('00000000-0000-0000-0000-000000000000')
        with self.assertRaises(StoreError):save_store({'name':'n','contactName':'a','contactPhone':'13800000000','address':'a','latitude':0})
        for body in [{'revision':1,'onSale':'bad'},{'revision':1,'stock':False},{'revision':999},{'revision':1,'stock':[{}]}]:
            with self.assertRaises(StoreError):update_product(self.store,self.product.id,body,self.member)
        self.assertAlmostEqual(distance_meters(0,0,0,0),0)
