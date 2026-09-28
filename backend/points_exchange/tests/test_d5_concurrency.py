"""Real PostgreSQL lock waits verify shared stock, funds and live authorization."""
import json,time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
from uuid import uuid4
from django.db import connection,connections,transaction
from django.test import TransactionTestCase,Client,override_settings
from django.utils import timezone
from payments.tests import test_payment_flow as payment_flow
from points_exchange.models import ExchangeOffer
from points_exchange.orders import create_exchange_quote,submit_exchange_order
from orders.service import OrderError
from orders.models import Order
from benefits.service import grant_points
from benefits.models import PointsAccount
from customers.models import Member,MemberSession
from catalog.models import Product


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',EXCHANGE_ORDER_ENABLED=True,
                   ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class D5ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        payment_flow.PaymentFlowTests.setUp(self)
        grant_points(self.member,1000,timezone.now()+timedelta(days=10),'d5-race')
        self.other=Member.objects.create(wechat_app_id=self.member.wechat_app_id,wechat_openid='d5-other',grade=self.member.grade)
        grant_points(self.other,1000,timezone.now()+timedelta(days=10),'d5-race-other')
        self.offer=ExchangeOffer.objects.create(sku=self.sku,points_price=600,status='ON_SALE')

    def quote(self,member=None):
        return create_exchange_quote(member or self.member,{'offerId':str(self.offer.id),'quantity':1})

    def race(self,functions):
        barrier=Barrier(len(functions))
        def runner(fn):
            connections.close_all()
            try:
                with connection.cursor() as cursor:cursor.execute("SET lock_timeout='5s'; SET statement_timeout='8s'")
                barrier.wait(timeout=5)
                try:return fn()
                except OrderError as exc:return exc.code
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            tasks=[pool.submit(runner,fn) for fn in functions]
            return [task.result(timeout=15) for task in tasks]

    def test_two_members_share_last_stock_unit(self):
        self.balance.on_hand_base_units=1;self.balance.save(update_fields=['on_hand_base_units'])
        one=self.quote();two=self.quote(self.other)
        result=self.race([lambda:submit_exchange_order(self.member,{'quoteId':one['quoteId']},uuid4()),
                          lambda:submit_exchange_order(self.other,{'quoteId':two['quoteId']},uuid4())])
        self.assertEqual(sum(isinstance(row,tuple) for row in result),1)
        self.assertEqual(Order.objects.count(),1)
        self.balance.refresh_from_db();self.assertEqual((self.balance.on_hand_base_units,self.balance.reserved_base_units),(0,0))
        self.assertEqual(sum(PointsAccount.objects.values_list('settled_points',flat=True)),1400)

    def test_same_member_different_keys_cannot_overspend_points(self):
        one=self.quote();two=self.quote()
        result=self.race([lambda:submit_exchange_order(self.member,{'quoteId':one['quoteId']},uuid4()),
                          lambda:submit_exchange_order(self.member,{'quoteId':two['quoteId']},uuid4())])
        self.assertEqual(sum(isinstance(row,tuple) for row in result),1)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,400)
        self.assertEqual(Order.objects.count(),1)

    def test_same_key_concurrently_commits_exactly_one_order(self):
        quote=self.quote();key=uuid4()
        result=self.race([lambda:submit_exchange_order(self.member,{'quoteId':quote['quoteId']},key),
                          lambda:submit_exchange_order(self.member,{'quoteId':quote['quoteId']},key)])
        self.assertEqual(result[0][0]['orderId'],result[1][0]['orderId'])
        self.assertEqual(sorted(row[1] for row in result),[False,True]);self.assertEqual(Order.objects.count(),1)

    def test_cash_order_reservation_and_exchange_compete_for_same_last_stock(self):
        from checkout.service import create_quote
        from orders.service import submit_order
        self.balance.on_hand_base_units=1;self.balance.save(update_fields=['on_hand_base_units'])
        points=self.quote();cash=create_quote({'items':[{'skuId':str(self.sku.id),'quantity':1}]},self.other)
        result=self.race([lambda:submit_exchange_order(self.member,{'quoteId':points['quoteId']},uuid4()),
                          lambda:submit_order(self.other,{'quoteId':cash['quoteId'],'paymentMethod':'OFFLINE'},uuid4())])
        self.assertEqual(sum(isinstance(row,tuple) for row in result),1);self.assertEqual(Order.objects.count(),1)
        self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units-self.balance.reserved_base_units,0)

    def wait_blocked(self,future,table):
        for _ in range(300):
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_stat_clear_snapshot()')
                cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE %s)",['%'+table+'%'])
                if cursor.fetchone()[0]:return
            if future.done():break
            time.sleep(.01)
        self.fail('request did not reach '+table+' lock; completed='+str(future.done()))

    def thread_call(self,fn):
        connections.close_all()
        try:
            with connection.cursor() as cursor:cursor.execute("SET lock_timeout='5s'; SET statement_timeout='8s'")
            return fn()
        finally:connections.close_all()

    def test_member_session_revoked_while_waiting_on_member_or_catalog_prevents_all_effects(self):
        quote=self.quote()
        for stage in ['member','catalog']:
            token=MemberSession.issue(self.member)[0]
            def write():
                return Client().post('/api/v1/app/exchange-orders',json.dumps({'quoteId':quote['quoteId']}),
                    content_type='application/json',HTTP_AUTHORIZATION='Bearer '+token,HTTP_IDEMPOTENCY_KEY=str(uuid4())).status_code
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    if stage=='member':Member.objects.select_for_update().get(pk=self.member.id)
                    else:Product.objects.select_for_update().get(pk=self.sku.product_id)
                    future=pool.submit(self.thread_call,write)
                    self.wait_blocked(future,'customer_member' if stage=='member' else 'product')
                    MemberSession.objects.filter(member=self.member,revoked_at__isnull=True).update(revoked_at=timezone.now())
                self.assertEqual(future.result(timeout=10),401)
            self.assertFalse(Order.objects.exists());self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,10)
            self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,1000)

    def test_admin_permission_or_auth_version_revoked_during_offer_wait_prevents_write(self):
        from accounts.models import AdminAccount,PermissionGroup,GroupPermission
        password='Synthetic D5 admin auth 2026!'
        for mode in ['permission','session']:
            actor=AdminAccount.objects.create_user('d5-'+mode,password,display_name='合成运营')
            group=PermissionGroup.objects.create(code='d5-'+mode,name='合成'+mode);actor.permission_groups.add(group)
            GroupPermission.objects.bulk_create([GroupPermission(group=group,code=code) for code in ['exchange.read','exchange.manage']])
            client=Client(enforce_csrf_checks=True);client.get('/api/v1/admin/auth/csrf')
            def write(path,body,verb='post'):
                return getattr(client,verb)(path,json.dumps(body),content_type='application/json',
                    HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value,HTTP_IDEMPOTENCY_KEY=str(uuid4()))
            self.assertEqual(write('/api/v1/admin/auth/login',{'loginName':actor.login_name,'password':password}).status_code,200)
            def edit():return write('/api/v1/admin/exchange-offers/'+str(self.offer.id),{'expectedRevision':1,'pointsPrice':100},'put').status_code
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    ExchangeOffer.objects.select_for_update().get(pk=self.offer.id)
                    future=pool.submit(self.thread_call,edit);self.wait_blocked(future,'points_exchange_offer')
                    if mode=='permission':GroupPermission.objects.filter(group=group,code='exchange.manage').delete()
                    else:AdminAccount.objects.filter(pk=actor.id).update(auth_version=actor.auth_version+1)
                self.assertEqual(future.result(timeout=10),403 if mode=='permission' else 401)
            self.offer.refresh_from_db();self.assertEqual((self.offer.points_price,self.offer.revision),(600,1))
