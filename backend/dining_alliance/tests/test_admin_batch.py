"""Bounded atomic dining administration and retirement of duplicate costs."""
from unittest.mock import patch
from uuid import uuid4
from django.test import TestCase,Client,override_settings
from accounts.models import AuditLog
from dining_alliance.models import Restaurant,ProductCommissionRule,StoreSkuPurchaseCost
from dining_alliance.tests import test_access_api as fixture
from catalog.models import Product,Category

ROOT='/api/v1/admin/dining-alliance'

@override_settings(WECHAT_MINI_APP_ID='wx-order-test',ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE':True})
class AdminBatchTests(TestCase):
    setUp=fixture.DiningAccessTests.setUp
    _sku=fixture.DiningAccessTests._sku
    make_store=fixture.DiningAccessTests.make_store
    scene=fixture.DiningAccessTests.scene
    admin_client=fixture.DiningAccessTests.admin_client
    write=fixture.DiningAccessTests.write

    def restaurants(self):
        store,first,_,_=self.scene()
        second=Restaurant.objects.create(name='第二店',address=first.address,latitude=first.latitude,longitude=first.longitude,assigned_store=store)
        return first,second

    def products(self):
        self.scene()
        second=Product.objects.create(product_no='BATCH',name='第二商品',category=self.product.category,fulfillment_kind='SHIP')
        return self.product,second

    def test_restaurant_batch_revision_atomic_audit(self):
        first,second=self.restaurants();client=self.admin_client()
        body={'enabled':False,'items':[{'restaurantId':str(row.pk),'expectedRevision':1} for row in [first,second]]}
        result=self.write(client,'post',ROOT+'/restaurants/batch-status',body)
        self.assertEqual(result.status_code,200,result.content)
        for row in [first,second]:
            row.refresh_from_db();self.assertFalse(row.enabled);self.assertEqual(row.revision,2)
        self.assertEqual(AuditLog.objects.filter(action_code='dining.restaurant.batch.status').count(),2)
        body['enabled']=True;body['items'][0]['expectedRevision']=2
        self.assertEqual(self.write(client,'post',ROOT+'/restaurants/batch-status',body).status_code,409)
        self.assertFalse(Restaurant.objects.filter(enabled=True).exists())
        self.assertEqual(AuditLog.objects.filter(action_code='dining.restaurant.batch.status').count(),2)

    def test_ratio_batch_existing_and_new_atomic_revision(self):
        first,second=self.products();client=self.admin_client()
        body={'commissionBps':0,'items':[{'productId':str(first.pk),'expectedRevision':1},{'productId':str(second.pk),'expectedRevision':0}]}
        result=self.write(client,'post',ROOT+'/product-ratios/batch',body)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(ProductCommissionRule.objects.get(product=first).revision,2)
        self.assertEqual(ProductCommissionRule.objects.get(product=second).revision,1)
        self.assertEqual(AuditLog.objects.filter(action_code='dining.ratio.batch.configure').count(),2)
        body['commissionBps']=2000;body['items'][0]['expectedRevision']=2
        self.assertEqual(self.write(client,'post',ROOT+'/product-ratios/batch',body).status_code,409)
        self.assertFalse(ProductCommissionRule.objects.exclude(commission_bps=0).exists())

    def test_batch_input_bounds_and_missing_rows_do_not_write(self):
        first,second=self.restaurants();client=self.admin_client()
        item={'restaurantId':str(first.pk),'expectedRevision':1}
        for items in [[],[item,item],[item]*101,[{'restaurantId':'bad','expectedRevision':1}],[{**item,'expectedRevision':True}],[{**item,'extra':1}]]:
            self.assertEqual(self.write(client,'post',ROOT+'/restaurants/batch-status',{'enabled':False,'items':items}).status_code,400)
        body={'enabled':False,'items':[item,{'restaurantId':str(uuid4()),'expectedRevision':1}]}
        self.assertEqual(self.write(client,'post',ROOT+'/restaurants/batch-status',body).status_code,404)
        self.assertTrue(Restaurant.objects.get(pk=first.pk).enabled)
        self.assertEqual(self.write(client,'post',ROOT+'/restaurants/batch-status',{'enabled':1,'items':[item]}).status_code,400)
        first,second=self.product,self.product
        item={'productId':str(first.pk),'expectedRevision':1}
        for ratio in [True,-1,10001,'20']:
            self.assertEqual(self.write(client,'post',ROOT+'/product-ratios/batch',{'commissionBps':ratio,'items':[item]}).status_code,400)

    def test_batch_live_permissions_csrf_and_read_only_staff(self):
        first,second=self.restaurants();client=self.admin_client()
        body={'enabled':False,'items':[{'restaurantId':str(first.pk),'expectedRevision':1}]}
        self.assertEqual(Client().post(ROOT+'/restaurants/batch-status',data=body).status_code,401)
        self.assertEqual(client.post(ROOT+'/restaurants/batch-status',data=body).status_code,403)
        from common.http import error
        with patch('dining_alliance.admin_batch.require_live',side_effect=lambda request,*a:(None,error(request,403,'PERMISSION_DENIED','权限已撤销'))):
            self.assertEqual(self.write(client,'post',ROOT+'/restaurants/batch-status',body).status_code,403)
        self.assertTrue(Restaurant.objects.get(pk=first.pk).enabled)
        from accounts.models import AdminAccount,PermissionGroup,GroupPermission,AccountGroup
        actor=AdminAccount.objects.create_user('dining-reader','Dining test secure 2026',kind='STAFF')
        group=PermissionGroup.objects.create(code='dining-reader',name='餐饮只读')
        GroupPermission.objects.create(group=group,code='dining.read');AccountGroup.objects.create(account=actor,group=group)
        client=Client(enforce_csrf_checks=True);client.get('/api/v1/admin/auth/csrf')
        self.write(client,'post','/api/v1/admin/auth/login',{'loginName':actor.login_name,'password':'Dining test secure 2026'})
        self.assertEqual(client.get(ROOT+'/categories').status_code,200)
        self.assertEqual(self.write(client,'post',ROOT+'/restaurants/batch-status',body).status_code,403)
        self.assertEqual(self.write(client,'put',ROOT+'/purchase-costs',{}).status_code,403)

    def test_ratio_category_parent_leaf_and_invalid_filter(self):
        first,second=self.products();client=self.admin_client()
        other=Category.objects.create(name='其他')
        Product.objects.create(product_no='OTHER-CAT',name='其他商品',category=other,fulfillment_kind='SHIP')
        for category in [first.category_id,first.category.parent_id]:
            result=client.get(ROOT+'/product-ratios',{'categoryId':str(category)})
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(result.json()['data']['total'],1)  # draft second product is excluded
            row=result.json()['data']['items'][0]
            self.assertEqual(row['categoryId'],str(first.category_id));self.assertEqual(row['productNo'],first.product_no)
        self.assertEqual(client.get(ROOT+'/product-ratios?categoryId=bad').status_code,400)
        self.assertEqual(client.get(ROOT+'/product-ratios?categoryId='+str(uuid4())).status_code,404)

    def test_duplicate_cost_write_retired_historical_rows_readable(self):
        store,_,_,_=self.scene();client=self.admin_client()
        row=StoreSkuPurchaseCost.objects.create(store=store,sku=self.skus[0],unit_version=self.skus[0].current_unit,purchase_cost_fen=123)
        for verb in ['put','patch']:
            result=self.write(client,verb,ROOT+'/purchase-costs',{'purchaseCostFen':456})
            self.assertEqual(result.status_code,410,result.content)
            self.assertEqual(result.json()['error']['code'],'DINING_COST_RETIRED')
        row.refresh_from_db();self.assertEqual(row.purchase_cost_fen,123)
        result=client.get(ROOT+'/purchase-costs').json()['data']
        self.assertTrue(result['retired']);self.assertEqual(result['items'][0]['purchaseCostFen'],123)

    def test_tables_batch_creates_all_or_none_and_validates_numbers(self):
        from dining_alliance.models import DiningTable
        _,restaurant,_,_=self.scene();client=self.admin_client()
        path=ROOT+f'/restaurants/{restaurant.pk}/tables/batch'
        result=self.write(client,'post',path,{'numbers':[' A1 ','B2'],'enabled':False})
        self.assertEqual(result.status_code,201,result.content)
        self.assertEqual([row['number'] for row in result.json()['data']['items']],['A1','B2'])
        self.assertFalse(DiningTable.objects.filter(restaurant=restaurant,number__in=['A1','B2'],enabled=True).exists())
        self.assertEqual(self.write(client,'post',path,{'numbers':['NEW','A1'],'enabled':True}).status_code,409)
        self.assertFalse(DiningTable.objects.filter(restaurant=restaurant,number='NEW').exists())
        for numbers in [[],['A',' A '],['A']*101,[''],[True],['x'*41]]:
            self.assertEqual(self.write(client,'post',path,{'numbers':numbers,'enabled':True}).status_code,400)
        self.assertEqual(AuditLog.objects.filter(action_code='dining.table.batch.create').count(),2)

    def test_table_mid_batch_failure_rolls_back_rows_and_audits(self):
        from dining_alliance.models import DiningTable
        from dining_alliance.access import DiningError
        from accounts.security import audit
        _,restaurant,_,_=self.scene();client=self.admin_client()
        calls=[]
        def interrupted(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise DiningError('模拟第二项审计失败。','DINING_CONFLICT',409)
            return audit(*args,**kwargs)
        with patch('dining_alliance.admin_batch.audit',side_effect=interrupted):
            result=self.write(client,'post',ROOT+f'/restaurants/{restaurant.pk}/tables/batch',{'numbers':['C1','C2'],'enabled':True})
        self.assertEqual(result.status_code,409,result.content)
        self.assertFalse(DiningTable.objects.filter(restaurant=restaurant,number__in=['C1','C2']).exists())
        self.assertFalse(AuditLog.objects.filter(action_code='dining.table.batch.create').exists())

    def test_ratio_search_supports_product_code(self):
        self.scene(); client=self.admin_client()
        result=client.get(ROOT+'/product-ratios',{'search':self.product.product_no})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual([row['productId'] for row in result.json()['data']['items']],[str(self.product.pk)])

    def test_restaurant_enabled_and_assigned_store_filters(self):
        first,second=self.restaurants();client=self.admin_client()
        second.enabled=False;second.save()
        other_store=self.make_store('FILTER-OTHER')
        second.assigned_store=other_store;second.save()
        for enabled,expected in [('true',first),('false',second)]:
            result=client.get(ROOT+'/restaurants',{'enabled':enabled})
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual([row['id'] for row in result.json()['data']['items']],[str(expected.pk)])
        result=client.get(ROOT+'/restaurants',{'storeId':str(other_store.pk),'enabled':'false'})
        self.assertEqual([row['id'] for row in result.json()['data']['items']],[str(second.pk)])
        self.assertEqual(client.get(ROOT+'/restaurants',{'storeId':str(uuid4())}).json()['data']['total'],0)
        for query in [('enabled','1'),('enabled',''),('enabled','TRUE'),('storeId','bad'),('storeId','')]:
            self.assertEqual(client.get(ROOT+'/restaurants',dict([query])).status_code,400)
        for key,value in [('enabled','true'),('storeId',str(other_store.pk))]:
            self.assertEqual(client.get(ROOT+'/restaurants',{key:[value,value]}).status_code,400)

    def test_ratio_configured_filters_include_zero_and_validate_query(self):
        first,second=self.products();client=self.admin_client()
        second.status='OFF_SALE';second.ever_on_sale=True;second.save()
        ProductCommissionRule.objects.filter(product=first).update(commission_bps=0)
        for configured,expected in [('true',first),('false',second)]:
            result=client.get(ROOT+'/product-ratios',{'configured':configured,'categoryId':str(first.category_id)})
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual([row['productId'] for row in result.json()['data']['items']],[str(expected.pk)])
        for value in ['','0','1','TRUE','yes']:
            self.assertEqual(client.get(ROOT+'/product-ratios',{'configured':value}).status_code,400)
        self.assertEqual(client.get(ROOT+'/product-ratios',{'configured':['true','false']}).status_code,400)
