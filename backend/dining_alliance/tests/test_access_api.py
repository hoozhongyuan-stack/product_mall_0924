import json
from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.db import transaction
from django.utils import timezone
from uuid import uuid4
from dining_alliance.models import RestaurantStaff, DiningSession, DiningTable, RestaurantProduct, ProductCommissionRule
from dining_alliance.access import checkout_context, DiningError, assert_products
from dining_alliance.service import save_restaurant, save_table, scan, products_data, update_product
from orders.tests import test_dining_checkout as fixture


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class DiningAccessTests(TestCase):
    setUp=fixture.DiningCheckoutTests.setUp
    _sku=fixture.DiningCheckoutTests._sku
    make_store=fixture.DiningCheckoutTests.make_store
    scene=fixture.DiningCheckoutTests.scene

    def test_scan_private_session_and_rescan_closes_previous(self):
        store,restaurant,table,identity=self.scene()
        with self.assertRaises(DiningError):checkout_context(identity,self.other)
        self.assertEqual(checkout_context(identity,self.member)[0],store)
        again=scan(self.member,table.code)['sessionId']
        with self.assertRaises(DiningError):checkout_context(identity,self.member)
        self.assertNotEqual(identity,again)
        DiningSession.objects.filter(pk=again).update(expires_at=timezone.now())
        with self.assertRaises(DiningError):checkout_context(again,self.member)

    def test_disable_table_restaurant_or_store_and_revision_require_rescan(self):
        store,restaurant,table,identity=self.scene()
        for target,field,value in [(table,'enabled',False),(restaurant,'enabled',False),(store,'accepting_orders',False),(store,'revision',2)]:
            old=getattr(target,field);setattr(target,field,value);target.save()
            with self.assertRaises(DiningError):checkout_context(identity,self.member)
            setattr(target,field,old);target.save()
        store.delivery_radius_meters=0;store.save()
        with self.assertRaises(DiningError):checkout_context(identity,self.member)

    def test_inventory_never_changes_and_only_listed_goods_available(self):
        from inventory.models import InventoryBalance
        store,restaurant,table,identity=self.scene(stock=3)
        before=list(InventoryBalance.objects.values())
        rows=products_data(restaurant,self.member,customer=True)
        self.assertEqual(rows[0]['skus'][0]['availableQuantity'],3)
        update_product(restaurant,{'productId':str(self.product.id),'onSale':False,'revision':1})
        self.assertEqual(products_data(restaurant,self.member,customer=True),[])
        with self.assertRaises(DiningError):assert_products(identity,self.member,[self.product.id])
        self.assertEqual(list(InventoryBalance.objects.values()),before)
        with self.assertRaises(DiningError):update_product(restaurant,{'productId':str(self.product.id),'onSale':True,'revision':1})
        with self.assertRaises(DiningError):update_product(restaurant,{'productId':str(self.product.id),'onSale':True,'revision':2,'stock':[]})

    def test_auto_match_and_table_unique_revisions(self):
        store,restaurant,table,identity=self.scene()
        row=save_restaurant({'requestKey':str(uuid4()),'name':'新联盟店','address':'街道','contactName':'店长','contactPhone':'13800000000','latitude':28.200001,'longitude':112.900001})
        self.assertEqual(row.assigned_store_id,store.id)
        changed=save_restaurant({'name':'新名称','revision':1},row.id)
        self.assertEqual(changed.revision,2)
        with self.assertRaises(DiningError):save_restaurant({'name':'旧修订','revision':1},row.id)
        second=save_table(row.id,{'number':'A1'})
        with self.assertRaises(DiningError):save_table(row.id,{'number':'A1'})
        changed=save_table(row.id,{'enabled':False,'revision':1},second.id)
        self.assertFalse(changed.enabled)
        with self.assertRaises(DiningError):save_table(row.id,{'enabled':True,'revision':1},second.id)
        with self.assertRaises(DiningError):save_restaurant({'requestKey':str(uuid4()),'name':'无仓店','address':'远处','contactName':'联系人','contactPhone':'13800000000','latitude':0,'longitude':0})

    def test_member_api_permissions_and_explicit_close(self):
        store,restaurant,table,identity=self.scene()
        base=f'/api/v1/app/dining-alliance/restaurants/{restaurant.id}'
        with patch('dining_alliance.views.require_member',return_value=(self.member,None)):
            self.assertEqual(self.client.get(base+'/products').status_code,403)
            RestaurantStaff.objects.create(restaurant=restaurant,member=self.member,permissions=['products','orders','finance'])
            self.assertEqual(self.client.get(base+'/products').status_code,200)
            self.assertEqual(self.client.get(base+'/account').status_code,200)
            self.assertEqual(self.client.get(base+'/orders').status_code,200)
            result=self.client.post('/api/v1/app/dining-alliance/scan',data=json.dumps({'tableCode':table.code}),content_type='application/json')
            self.assertEqual(result.status_code,201,result.content)
            identity=result.json()['data']['sessionId']
            path=f'/api/v1/app/dining-alliance/sessions/{identity}'
            self.assertEqual(self.client.get(path+'/products').status_code,200)
            self.assertEqual(self.client.delete(path).status_code,200)
            self.assertEqual(self.client.get(path+'/products').status_code,403)
        self.assertEqual(self.client.get(base+'/products').status_code,401)

    def test_admin_cookie_csrf_audit_and_code_permissions(self):
        from accounts.models import AdminAccount, AuditLog
        owner=AdminAccount.objects.get(kind='OWNER')
        owner.login_name='dining-owner';owner.set_password('Dining test secure 2026');owner.save()
        client=Client(enforce_csrf_checks=True)
        client.get('/api/v1/admin/auth/csrf')
        token=client.cookies['csrftoken'].value
        result=client.post('/api/v1/admin/auth/login',data=json.dumps({'loginName':'dining-owner','password':'Dining test secure 2026'}),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code,200,result.content)
        store,restaurant,table,identity=self.scene()
        base=f'/api/v1/admin/dining-alliance/restaurants/{restaurant.id}'
        self.assertEqual(client.post(base+'/tables',data=json.dumps({'number':'B1'}),content_type='application/json').status_code,403)
        token=client.cookies['csrftoken'].value
        result=client.post(base+'/tables',data=json.dumps({'number':'B1'}),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code,201,result.content)
        self.assertTrue(AuditLog.objects.filter(action_code='dining.table.save').exists())
        from pathlib import Path
        for extension,mime in [('png','image/png'),('jpg','image/jpeg')]:
            raw=(Path(__file__).parent/f'fixtures/wechat-code.{extension}').read_bytes()
            with patch('dining_alliance.code_views.generate_code',return_value=raw):
                result=client.get(base+f'/tables/{table.id}/code')
                self.assertEqual(result.status_code,200)
                self.assertEqual(result['Content-Type'],mime)
                self.assertEqual(result.content,raw)
                self.assertIn(f'{table.id}.{extension}',result['Content-Disposition'])
        self.assertEqual(Client().get(base+f'/tables/{table.id}/code').status_code,401)

    def test_code_configuration_errors_and_bounds(self):
        from dining_alliance.code_views import generate_code
        from types import SimpleNamespace
        with patch('wechat_integration.credentials.effective_credentials',return_value=SimpleNamespace(app_id='',secret='')):
            with self.assertRaises(DiningError):generate_code('a'*32)
        for value in ['', 'http://evil.example', None, True, 'a'*33]:
            with self.assertRaises(DiningError):scan(self.member,value)

    def admin_client(self):
        self.owner.set_password('Dining test secure 2026');self.owner.save()
        client=Client(enforce_csrf_checks=True)
        client.get('/api/v1/admin/auth/csrf')
        client.post('/api/v1/admin/auth/login',data=json.dumps({'loginName':self.owner.login_name,'password':'Dining test secure 2026'}),content_type='application/json',HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        return client

    def write(self,client,method,path,body):
        return getattr(client,method)(path,data=json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)

    def test_admin_restaurant_staff_ratio_cost_contract_and_validation(self):
        store,restaurant,table,identity=self.scene()
        client=self.admin_client()
        root='/api/v1/admin/dining-alliance'
        base=f'{root}/restaurants/{restaurant.id}'
        for path in [root+'/restaurants',base,base+'/tables',base+'/staff',root+'/product-ratios',root+'/purchase-costs',root+f'/purchase-costs?storeId={store.id}',root+'/orders',base+'/account',base+'/withdrawals']:
            result=client.get(path)
            self.assertEqual(result.status_code,200,(path,result.content))
        self.assertIn('meta',client.get(root+'/restaurants').json())
        body={'requestKey':str(uuid4()),'name':'测试新店','address':'8号','contactName':'店长','contactPhone':'13800000000','latitude':28.200001,'longitude':112.900001}
        result=self.write(client,'post',root+'/restaurants',body);self.assertEqual(result.status_code,201,result.content)
        self.assertEqual(self.write(client,'patch',base,{'name':'更新店名','revision':1}).status_code,200)
        staff={'revision':0,'memberId':str(self.member.id),'permissions':['products','orders','finance'],'enabled':True}
        result=self.write(client,'post',base+'/staff',staff);self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(client.get(base+'/staff?memberSearch='+self.member.member_no).status_code,200)
        self.assertEqual(self.write(client,'patch',base+f'/tables/{table.id}',{'number':'A99','revision':1}).status_code,200)
        ratio={'productId':str(self.product.id),'commissionBps':1500,'revision':1}
        self.assertEqual(self.write(client,'put',root+'/product-ratios',ratio).status_code,200)
        self.assertEqual(self.write(client,'put',root+'/product-ratios',ratio).status_code,409)
        cost={'storeId':str(store.id),'skuId':str(self.skus[0].id),'unitVersionId':str(self.skus[0].current_unit_id),'purchaseCostFen':600,'revision':0}
        self.assertEqual(self.write(client,'put',root+'/purchase-costs',cost).status_code,410)
        self.assertEqual(self.write(client,'patch',root+'/purchase-costs',cost).status_code,410)
        for path,body in [(root+'/product-ratios',{'commissionBps':True}),(base+'/staff',{'memberId':str(self.member.id),'permissions':['inventory'],'enabled':True,'revision':0})]:
            self.assertEqual(self.write(client,'post' if path.endswith('staff') else 'put',path,body).status_code,400)
        self.assertEqual(client.get(root+'/restaurants?page=0').status_code,400)
        self.assertEqual(client.get(root+'/restaurants?pageSize=101').status_code,400)
        self.assertEqual(client.delete(base,HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value).status_code,405)

    def test_member_manage_scan_pagination_and_errors(self):
        store,restaurant,table,identity=self.scene()
        RestaurantStaff.objects.create(restaurant=restaurant,member=self.member,permissions=['products','orders','finance'])
        with patch('dining_alliance.views.require_member',return_value=(self.member,None)):
            root='/api/v1/app/dining-alliance'
            base=f'{root}/restaurants/{restaurant.id}'
            self.assertEqual(self.client.get(root+'/restaurants').status_code,200)
            body={'productId':str(self.product.id),'onSale':False,'revision':1}
            result=self.client.patch(base+'/products',data=json.dumps(body),content_type='application/json');self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(self.client.get(base+'/withdrawals').status_code,200)
            self.assertEqual(self.client.post(base+'/withdrawals',data=json.dumps({'amountFen':10,'requestKey':'bad'}),content_type='application/json').status_code,400)
            self.assertEqual(self.client.post(base+'/withdrawals',data=json.dumps({'amountFen':True,'requestKey':'bad'}),content_type='application/json').status_code,400)
            self.assertEqual(self.client.get(base+'/products?pageSize=0').status_code,400)
            self.assertEqual(self.client.get(base+'/orders/'+str(restaurant.id)).status_code,404)
            self.assertEqual(self.client.post(root+'/scan',data=json.dumps({'tableCode':table.code,'restaurantId':str(restaurant.id)}),content_type='application/json').status_code,400)
            self.assertEqual(self.client.get(f'{root}/sessions/{identity}/products?page=10001').status_code,400)

    def test_actual_code_transport_uses_registered_mini_program_path(self):
        from dining_alliance.code_views import generate_code
        from types import SimpleNamespace
        from pathlib import Path
        import io
        result=io.BytesIO((Path(__file__).parent/'fixtures/wechat-code.png').read_bytes())
        class Reply:
            def __enter__(self):return result
            def __exit__(self,*args):return False
        with patch('wechat_integration.credentials.effective_credentials',return_value=SimpleNamespace(app_id='wx-test',secret='secret')),patch('wechat_integration.probe.acquire_access_token',return_value=SimpleNamespace(status='SUCCESS',token='test-token')),patch('dining_alliance.code_views.build_opener') as opener:
            opener.return_value.open.return_value=Reply()
            self.assertTrue(generate_code('a'*32).startswith(b'\x89PNG'))
            request=opener.return_value.open.call_args.args[0]
            payload=json.loads(request.data)
            app=json.loads((Path(__file__).parents[3]/'mini-program/app.json').read_text())
            self.assertIn(payload['page'],app['pages'])
            self.assertEqual(payload['scene'],'a'*32)
            self.assertTrue(payload['check_path'])

    def test_finance_read_alone_can_choose_restaurants_without_store_manage(self):
        from accounts.models import AdminAccount, PermissionGroup, GroupPermission, AccountGroup
        store,restaurant,table,identity=self.scene()
        actor=AdminAccount.objects.create_user('dining-finance','Dining test secure 2026',kind='STAFF')
        group=PermissionGroup.objects.create(code='dining-finance',name='餐饮财务')
        GroupPermission.objects.create(group=group,code='dining.finance.read')
        AccountGroup.objects.create(account=actor,group=group)
        client=Client(enforce_csrf_checks=True);client.get('/api/v1/admin/auth/csrf')
        self.write(client,'post','/api/v1/admin/auth/login',{'loginName':actor.login_name,'password':'Dining test secure 2026'})
        root='/api/v1/admin/dining-alliance'
        self.assertEqual(client.get(root+'/restaurants').status_code,200)
        self.assertEqual(client.get(root+f'/restaurants/{restaurant.id}/account').status_code,200)
        self.assertEqual(client.get(root+f'/restaurants/{restaurant.id}/tables').status_code,403)
        self.assertEqual(self.write(client,'post',root+'/restaurants',{}).status_code,403)

    def test_restaurant_creation_key_and_staff_revision_block_stale_writes(self):
        store,restaurant,table,identity=self.scene()
        client=self.admin_client();root='/api/v1/admin/dining-alliance'
        body={'requestKey':str(uuid4()),'name':'幂等店','address':'8号','contactName':'店长','contactPhone':'13800000000','latitude':28.200001,'longitude':112.900001}
        first=self.write(client,'post',root+'/restaurants',body)
        again=self.write(client,'post',root+'/restaurants',body)
        self.assertEqual(first.status_code,201,first.content)
        self.assertEqual(first.json()['data']['id'],again.json()['data']['id'])
        self.assertEqual(self.write(client,'post',root+'/restaurants',{**body,'name':'重复冲突'}).status_code,409)
        path=root+f'/restaurants/{restaurant.id}/staff'
        body={'memberId':str(self.member.id),'permissions':['products'],'enabled':True,'revision':0}
        self.assertEqual(self.write(client,'post',path,body).status_code,200)
        self.assertEqual(self.write(client,'post',path,{**body,'enabled':False,'revision':1}).status_code,200)
        self.assertEqual(self.write(client,'post',path,{**body,'revision':1}).status_code,409)
        self.assertFalse(RestaurantStaff.objects.get(restaurant=restaurant,member=self.member).enabled)

    def test_sale_filter_precedes_pagination_after_hundred_hidden_goods(self):
        from catalog.models import Product,Sku,SkuUnitVersion
        store,restaurant,table,identity=self.scene()
        for index in range(101):
            row=Product.objects.create(product_no=f'HIDDEN-{index}',name=f'AAA-{index:03}',category=self.product.category,fulfillment_kind='SHIP',status='ON_SALE',ever_on_sale=True)
            sku=Sku.objects.create(product=row,sku_code=f'HIDDEN-{index}',spec_key='unit',list_price_fen=1,sale_status='ON_SALE')
            unit=SkuUnitVersion.objects.create(sku=sku,base_unit='瓶',sale_unit='瓶',ratio=1)
            sku.current_unit=unit;sku.save()
        self.assertEqual(products_data(restaurant,self.member,customer=True)[0]['id'],str(self.product.pk))
        with patch('dining_alliance.views.require_member',return_value=(self.member,None)):
            result=self.client.get(f'/api/v1/app/dining-alliance/sessions/{identity}/products?pageSize=1')
            self.assertEqual(result.json()['data']['total'],1)
            self.assertEqual(result.json()['data']['items'][0]['id'],str(self.product.pk))
