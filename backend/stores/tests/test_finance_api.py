import json
from django.test import Client
from stores.tests.test_finance import WithdrawalTests
from accounts.models import AdminAccount,AuditLog


class FinanceApiTests(WithdrawalTests):
    def setUp(self):
        super().setUp()
        self.owner=AdminAccount.objects.create_user('finance-owner','Finance safe password 2026',kind='OWNER')
        self.client=Client(enforce_csrf_checks=True)
        self.client.get('/api/v1/admin/auth/csrf')
        self.post('/api/v1/admin/auth/login',{'loginName':'finance-owner','password':'Finance safe password 2026'})

    def post(self,path,body,**headers):
        return self.client.post(path,data=json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,**headers)

    def confirm(self,action,object_id,revision):
        response=self.post('/api/v1/admin/auth/confirm',{'action':action,'objectId':str(object_id),'revision':revision,'password':'Finance safe password 2026'})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['data']['confirmationToken']

    def test_withdrawal_admin_confirmation_scope_and_payout_evidence(self):
        from stores.withdrawals import request_withdrawal
        body=self.body();row,_=request_withdrawal(self.store,self.member,body)
        path=f'/api/v1/admin/stores/{self.store.pk}/withdrawals/{row.pk}'
        reveal={'expectedRevision':1}
        self.assertEqual(Client().post(path+'/payee',data=json.dumps(reveal),content_type='application/json').status_code,401)
        self.assertEqual(self.post(path+'/payee',reveal).status_code,403)
        token=self.confirm('stores.withdrawal.payee',row.pk,1)
        response=self.post(path+'/payee',reveal,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(response.status_code,200,response.content)
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response.json()['data']['bankAccount'],body['bankAccount'])
        self.assertNotIn(body['bankAccount'],str(list(AuditLog.objects.values('before','after'))))
        account=self.client.get(f'/api/v1/admin/stores/{self.store.pk}/account')
        self.assertNotIn(body['bankAccount'],account.content.decode())
        account_list=self.client.get('/api/v1/admin/stores/accounts').json()['data']['items']
        summary=next(item for item in account_list if item['storeId']==str(self.store.pk))
        self.assertEqual(summary['income'],[])
        self.assertEqual(summary['withdrawals'],[])
        self.assertEqual(summary['balance'],account.json()['data']['balance'])
        self.assertEqual(len(account.json()['data']['withdrawals']),1)
        self.assertIn('no-store',account['Cache-Control'])
        approve={'expectedRevision':1,'decision':'APPROVE','reason':''}
        token=self.confirm('stores.withdrawal.review',row.pk,1)
        response=self.post(path+'/review',approve,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['data']['status'],'APPROVED_PENDING_PAYMENT')
        pay={'expectedRevision':2,'paymentReference':'TEST-BANK-0001','reason':'已线下转账'}
        token=self.confirm('stores.withdrawal.pay',row.pk,2)
        response=self.post(path+'/pay',pay,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['data']['status'],'PAID')
        self.assertEqual(self.post(path+'/pay',pay).status_code,409)

    def test_rule_cost_price_bounds_confirmation_and_reload(self):
        from catalog.models import Category,Product,Sku,SkuUnitVersion
        product=Product.objects.create(name='财务商品',product_no='FIN-P',category=Category.objects.create(name='财务类目'),fulfillment_kind='SHIP')
        sku=Sku.objects.create(product=product,sku_code='FIN-SKU',spec_key='瓶',list_price_fen=10000)
        unit=SkuUnitVersion.objects.create(sku=sku,base_unit='瓶',sale_unit='瓶',ratio=1)
        sku.current_unit=unit;sku.save()
        path=f'/api/v1/admin/stores/profit-rules/{sku.pk}'
        def put(body,token=''):
            return self.client.put(path,data=json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,HTTP_X_ACTION_CONFIRMATION=token)
        body={'expectedRevision':0,'purchaseCostFen':6000,'platformShareBps':2000}
        self.assertEqual(Client().get('/api/v1/admin/stores/profit-rules').status_code,401)
        self.assertEqual(self.client.put(path,data=json.dumps(body),content_type='application/json').status_code,403)
        self.assertEqual(put(body).status_code,403)
        response=put(body,self.confirm('stores.profit.configure',sku.pk,0))
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()['data'];self.assertEqual(data['purchaseCostFen'],6000);self.assertEqual(data['revision'],1)
        response=self.client.get('/api/v1/admin/stores/profit-rules?search=FIN-SKU')
        self.assertEqual(response.json()['data']['items'][0],data)
        self.assertEqual(put(body,self.confirm('stores.profit.configure',sku.pk,0)).status_code,409)
        for invalid in [{**body,'purchaseCostFen':-1},{**body,'platformShareBps':10001},{**body,'expectedRevision':True}]:self.assertEqual(put(invalid).status_code,400)

    def test_profit_rules_show_human_specifications_without_per_row_queries(self):
        from catalog.models import Category,Product,Sku,SpecAxis,SpecOption,SkuSpecSelection
        product=Product.objects.create(name='多规格商品',product_no='MULTI-P',category=Category.objects.create(name='规格类目'),fulfillment_kind='SHIP')
        package=SpecAxis.objects.create(product=product,name='包装',sort_order=0)
        size=SpecAxis.objects.create(product=product,name='容量',sort_order=1)
        box=SpecOption.objects.create(axis=package,value='整箱',sort_order=0)
        ml=SpecOption.objects.create(axis=size,value='330ml × 24',sort_order=0)
        sku=Sku.objects.create(product=product,sku_code='MULTI-SKU',spec_key=f'{box.pk}:{ml.pk}',list_price_fen=10000)
        SkuSpecSelection.objects.create(sku=sku,axis=package,option=box)
        SkuSpecSelection.objects.create(sku=sku,axis=size,option=ml)
        result=self.client.get('/api/v1/admin/stores/profit-rules?search=MULTI-SKU').json()['data']['items'][0]
        self.assertEqual(result['specLabel'],'包装：整箱 / 容量：330ml × 24')
        self.assertEqual(result['specKey'],sku.spec_key)
        self.assertNotIn(str(box.pk),result['specLabel'])
        from catalog.spec_read import specification_prefetch,specification_label
        rows=list(Sku.objects.filter(pk=sku.pk).prefetch_related(specification_prefetch()))
        with self.assertNumQueries(0):self.assertEqual(specification_label(rows[0]),result['specLabel'])
