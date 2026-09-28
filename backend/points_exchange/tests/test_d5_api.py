import json
from datetime import timedelta
from uuid import uuid4
from django.db import connection
from django.test import TestCase,Client,override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from payments.tests import test_payment_flow as payment_flow
from accounts.models import AdminAccount,PermissionGroup,GroupPermission,AuditLog
from customers.models import Member,MemberSession,CustomerAddress
from points_exchange.models import ExchangeOffer,ExchangeQuote,ExchangeOperation
from points_exchange.orders import create_exchange_quote
from benefits.service import grant_points

PASSWORD='Long test password 2026!'
BASE='/api/v1/admin/exchange-offers'


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',EXCHANGE_ORDER_ENABLED=True)
class D5ApiTests(TestCase):
    def setUp(self):
        payment_flow.PaymentFlowTests.setUp(self)
        self.owner=AdminAccount.objects.get(login_name='payment-owner')
        self.client=self.login(self.owner)
        grant_points(self.member,1000,timezone.now()+timedelta(days=10),'synthetic-d5')
        self.token=MemberSession.issue(self.member)[0]
        self.app=Client(enforce_csrf_checks=True)

    def login(self,actor):
        client=Client(enforce_csrf_checks=True);client.get('/api/v1/admin/auth/csrf')
        result=self.write(client,'post','/api/v1/admin/auth/login',{'loginName':actor.login_name,'password':PASSWORD})
        self.assertEqual(result.status_code,200,result.content)
        return client

    def write(self,client,verb,path,body,key=None,confirmation=None):
        headers={'HTTP_X_CSRFTOKEN':client.cookies['csrftoken'].value}
        if key:headers['HTTP_IDEMPOTENCY_KEY']=str(key)
        if confirmation:headers['HTTP_X_ACTION_CONFIRMATION']=confirmation
        return getattr(client,verb)(path,json.dumps(body),content_type='application/json',**headers)

    def staff(self,codes):
        actor=AdminAccount.objects.create_user('d5-staff',PASSWORD,display_name='合成只读')
        group=PermissionGroup.objects.create(code='d5',name='合成D5')
        actor.permission_groups.add(group)
        GroupPermission.objects.bulk_create([GroupPermission(group=group,code=code) for code in codes])
        return self.login(actor),group,actor

    def app_get(self,path,params=None,token=None):
        return self.app.get('/api/v1/app/'+path,params or {},HTTP_AUTHORIZATION='Bearer '+(token or self.token))

    def app_post(self,path,body,key=None,token=None):
        return self.app.post('/api/v1/app/'+path,json.dumps(body),content_type='application/json',
            HTTP_AUTHORIZATION='Bearer '+(token or self.token),HTTP_IDEMPOTENCY_KEY=str(key or uuid4()))

    def offer(self):
        return ExchangeOffer.objects.create(sku=self.sku,points_price=100,status='ON_SALE')

    def confirm(self,offer,client=None,object_id=None):
        result=self.write(client or self.client,'post','/api/v1/admin/auth/confirm',{'action':'exchange.publish',
            'password':PASSWORD,'objectId':str(object_id or offer.id),'revision':offer.revision})
        self.assertEqual(result.status_code,200,result.content)
        return result.json()['data']['confirmationToken']

    def test_admin_create_edit_exact_recovery_replay_and_conflicting_body(self):
        body={'skuId':str(self.sku.id),'pointsPrice':101};key=uuid4()
        first=self.write(self.client,'post',BASE,body,key)
        self.assertEqual(first.status_code,201,first.content)
        second=self.write(self.client,'post',BASE,body,key)
        self.assertEqual(first.json()['data'],second.json()['data'])
        result=self.client.get('/api/v1/admin/exchange-operations/'+str(key)).json()['data']
        self.assertEqual(result,{'status':'COMPLETED','result':first.json()['data']})
        self.assertEqual(self.write(self.client,'post',BASE,{**body,'pointsPrice':102},key).status_code,409)
        offer=ExchangeOffer.objects.get();path=BASE+'/'+str(offer.id)
        self.assertEqual(self.write(self.client,'put',path,{'expectedRevision':2,'pointsPrice':201},uuid4()).status_code,409)
        self.assertEqual(self.write(self.client,'put',path,{'expectedRevision':1,'pointsPrice':201},uuid4()).status_code,200)
        self.assertEqual(AuditLog.objects.filter(action_code='exchange.create').count(),1)

    def test_availability_csrf_password_target_and_replay(self):
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=100);path=BASE+'/'+str(offer.id)+'/availability'
        body={'expectedRevision':1,'status':'ON_SALE'}
        self.assertEqual(self.client.post(path,json.dumps(body),content_type='application/json').status_code,403)
        self.assertEqual(self.write(self.client,'post',path,body,uuid4()).status_code,403)
        self.assertEqual(self.write(self.client,'post',path,body,uuid4(),self.confirm(offer,object_id=uuid4())).status_code,403)
        token=self.confirm(offer);key=uuid4();first=self.write(self.client,'post',path,body,key,token)
        self.assertEqual(first.status_code,200,first.content)
        self.assertEqual(self.write(self.client,'post',path,body,key,token).json()['data'],first.json()['data'])

    def test_readonly_live_revocation_and_actor_private_recovery(self):
        key=uuid4();self.write(self.client,'post',BASE,{'skuId':str(self.sku.id),'pointsPrice':100},key)
        reader,group,actor=self.staff(['exchange.read'])
        self.assertEqual(reader.get(BASE).status_code,200)
        self.assertEqual(self.write(reader,'post',BASE,{'skuId':str(self.sku.id),'pointsPrice':100},uuid4()).status_code,403)
        self.assertEqual(reader.get('/api/v1/admin/exchange-operations/'+str(key)).json()['data'],{'status':'NOT_FOUND'})
        GroupPermission.objects.filter(group=group).delete()
        self.assertEqual(reader.get(BASE).status_code,403)

    def test_public_browse_and_member_only_quote_order(self):
        offer=self.offer()
        result=Client().get('/api/v1/app/exchange-products')
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['pagination']['total'],1)
        self.assertIn('no-store',result['Cache-Control'])
        self.assertNotIn('listPriceFen',result.content.decode())
        self.assertEqual(Client().post('/api/v1/app/exchange-quotes',json.dumps({'offerId':str(offer.id),'quantity':1}),content_type='application/json').status_code,401)
        quote=self.app_post('exchange-quotes',{'offerId':str(offer.id),'quantity':2}).json()['data']
        key=uuid4();first=self.app_post('exchange-orders',{'quoteId':quote['quoteId']},key)
        self.assertEqual(first.status_code,201,first.content)
        self.assertEqual(first.json()['data']['exchangePoints'],200)
        second=self.app_post('exchange-orders',{'quoteId':quote['quoteId']},key)
        self.assertEqual(second.status_code,200)
        result=self.app_get('exchange-operations/'+str(key)).json()['data']
        self.assertEqual(result['status'],'COMPLETED');self.assertEqual(result['result']['orderId'],first.json()['data']['orderId'])

    def test_quote_and_recovery_are_member_scoped_and_session_revocation_denies(self):
        offer=self.offer();quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        other=Member.objects.create(wechat_app_id=self.member.wechat_app_id,wechat_openid='synthetic-other',grade=self.member.grade)
        token=MemberSession.issue(other)[0];key=uuid4()
        self.assertEqual(self.app_post('exchange-orders',{'quoteId':quote['quoteId']},key,token).status_code,404)
        self.app_post('exchange-orders',{'quoteId':quote['quoteId']},key)
        self.assertEqual(self.app_get('exchange-operations/'+str(key),token=token).json()['data'],{'status':'NOT_FOUND'})
        MemberSession.objects.filter(member=self.member).update(revoked_at=timezone.now())
        self.assertEqual(self.app_post('exchange-orders',{'quoteId':quote['quoteId']},key).status_code,401)
        self.assertEqual(self.app_get('exchange-operations/'+str(key)).status_code,401)

    def test_input_bounds_query_duplicates_and_current_unavailable_structure(self):
        offer=self.offer()
        for quantity in [True,0,100,-1,'1']:
            self.assertEqual(self.app_post('exchange-quotes',{'offerId':str(offer.id),'quantity':quantity}).status_code,400)
        self.assertEqual(self.app_post('exchange-quotes',{'offerId':str(offer.id),'quantity':1,'pointsToUse':1}).status_code,400)
        for params in [{'pageSize':101},{'page':0},{'q':'x'*101},{'page':['1','2']},{'unknown':'x'}]:
            self.assertEqual(self.app_get('exchange-products',params).status_code,400)
        self.sku.product.redeem_valid_until=timezone.localdate()-timedelta(days=1)
        self.sku.product.save(update_fields=['redeem_valid_until'])
        self.assertEqual(self.app_get('exchange-products/'+str(offer.id)).status_code,404)
        self.assertEqual(self.app_post('exchange-quotes',{'offerId':str(offer.id),'quantity':1}).status_code,409)

    def test_gate_closed_quote_shows_distinct_availability(self):
        offer=self.offer()
        with override_settings(EXCHANGE_ORDER_ENABLED=False):
            quote=self.app_post('exchange-quotes',{'offerId':str(offer.id),'quantity':1}).json()['data']
            self.assertTrue(quote['ready']);self.assertFalse(quote['exchangeOrderAvailable'])
            self.assertEqual(self.app_post('exchange-orders',{'quoteId':quote['quoteId']}).status_code,503)

    def test_exchange_sku_picker_permission_independent_of_catalog_read(self):
        reader,group,actor=self.staff(['exchange.read','exchange.manage'])
        result=reader.get('/api/v1/admin/exchange-sku-options',{'q':'PAY'})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['items'][0]['skuId'],str(self.sku.id))
        self.assertNotIn('listPriceFen',result.content.decode())
        for value in [0,True,1000001]:
            self.assertEqual(self.write(reader,'post',BASE,{'skuId':str(self.sku.id),'pointsPrice':value},uuid4()).status_code,400)

    def test_structurally_invalid_draft_or_category_or_unit_cannot_publish(self):
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=100)
        category=self.sku.product.category;category.status='INACTIVE';category.save(update_fields=['status'])
        result=self.write(self.client,'post',BASE+'/'+str(offer.id)+'/availability',{'expectedRevision':1,'status':'ON_SALE'},uuid4(),self.confirm(offer))
        self.assertEqual(result.status_code,409,result.content)
        self.assertFalse(Client().get('/api/v1/app/exchange-products').json()['data']['items'])

    def test_points_order_filter_and_server_computed_after_sale_points(self):
        offer=self.offer();quote=self.app_post('exchange-quotes',{'offerId':str(offer.id),'quantity':2}).json()['data']
        data=self.app_post('exchange-orders',{'quoteId':quote['quoteId']}).json()['data']
        result=self.app_get('orders',{'orderKind':'POINTS'})
        self.assertEqual(result.json()['data']['total'],1)
        self.assertEqual(self.app_get('orders',{'orderKind':'CASH'}).json()['data']['total'],0)
        from aftersales.api_read import options,preview
        result=options(self.member,data['orderId'])
        self.assertEqual(result['orderKind'],'POINTS')
        self.assertEqual(result['items'][0]['options'][0]['maxPoints'],200)
        self.assertEqual(preview(self.member,data['items'][0]['orderLineId'],'REFUND_ONLY','UNUSED',1)['refundPoints'],100)

    def test_pause_removes_public_media_when_cash_listing_is_off(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from points_exchange.media_access import asset_visible_for_exchange
        offer=self.offer()
        self.sku.sale_status='OFF_SALE';self.sku.save(update_fields=['sale_status'])
        self.sku.product.status='OFF_SALE';self.sku.product.save(update_fields=['status'])
        self.assertTrue(asset_visible_for_exchange(self.sku.product.main_image_id))
        with TemporaryDirectory() as temporary:
            image=Path(temporary)/'test.png';image.write_bytes(b'synthetic-image')
            with patch('catalog.media.asset_path',return_value=image):
                result=Client().get('/api/v1/app/assets/'+str(self.sku.product.main_image_id)+'/file')
                self.assertEqual(result.status_code,200)
                self.assertEqual(b''.join(result.streaming_content),b'synthetic-image')
                offer.status='OFF_SALE';offer.revision+=1;offer.save(update_fields=['status','revision'])
                self.assertEqual(Client().get('/api/v1/app/assets/'+str(self.sku.product.main_image_id)+'/file').status_code,404)

    def test_detail_recovery_method_and_shape_errors_are_private_and_bounded(self):
        offer=self.offer();admin=BASE+'/'+str(offer.id)
        self.assertEqual(self.client.get(admin).status_code,200)
        self.assertEqual(self.client.get(admin,{'unexpected':'1'}).status_code,400)
        self.assertEqual(self.app_get('exchange-products/'+str(offer.id)).status_code,200)
        self.assertEqual(self.app_get('exchange-products/'+str(offer.id),{'unexpected':'1'}).status_code,400)
        self.assertEqual(self.client.get('/api/v1/admin/exchange-operations/'+str(uuid4()),{'unexpected':'1'}).status_code,400)
        self.assertEqual(self.app_get('exchange-operations/'+str(uuid4()),{'unexpected':'1'}).status_code,400)
        self.assertEqual(self.app_post('exchange-orders',{'quoteId':str(uuid4()),'quantity':1}).status_code,400)
        self.assertEqual(self.app_get('exchange-products/'+str(uuid4())).status_code,404)
        result=self.app.post('/api/v1/app/exchange-products')
        self.assertEqual(result.status_code,405);self.assertIn('no-store',result['Cache-Control'])
        self.assertEqual(self.client.get(BASE,{'status':'UNKNOWN'}).status_code,400)
        self.assertEqual(self.client.get(BASE,{'q':'PAY','status':'ON_SALE'}).json()['data']['pagination']['total'],1)
        self.assertEqual(self.write(self.client,'put',admin,{'pointsPrice':3},uuid4()).status_code,400)
        self.assertEqual(self.app_post('exchange-quotes',{'offerId':'malformed','quantity':1}).status_code,400)

    def test_quote_and_admin_write_rate_limits_keep_exact_replay_available(self):
        offer=self.offer()
        for _ in range(60):create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        self.assertEqual(self.app_post('exchange-quotes',{'offerId':str(offer.id),'quantity':1}).status_code,429)
        key=uuid4();body={'expectedRevision':1,'pointsPrice':200}
        first=self.write(self.client,'put',BASE+'/'+str(offer.id),body,key)
        self.assertEqual(first.status_code,200)
        ExchangeOperation.objects.bulk_create([ExchangeOperation(actor=self.owner,key=uuid4(),action='edit',offer=offer,digest='x'*64,result={}) for _ in range(59)])
        self.assertEqual(self.write(self.client,'put',BASE+'/'+str(offer.id),{'expectedRevision':2,'pointsPrice':300},uuid4()).status_code,429)
        self.assertEqual(self.write(self.client,'put',BASE+'/'+str(offer.id),body,key).json()['data'],first.json()['data'])
