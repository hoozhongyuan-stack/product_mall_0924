"""PostgreSQL threads verify quota and established member-before-coupon locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4
from django.db import connection,connections,transaction
from django.test import TransactionTestCase,override_settings
from django.utils import timezone
from benefits.coupon_operations import create_campaign,publish_campaign,claim_coupon
from benefits.models import CouponCampaign,CouponIssuance,CouponAllocation,MemberCoupon
from benefits.service import BenefitError,reserve_benefits,consume_benefits,release_benefits
from catalog.models import MemberGrade
from customers.models import Member


@override_settings(WECHAT_MINI_APP_ID='wx-d41-concurrency')
class D41ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        grade,_=MemberGrade.objects.get_or_create(code='normal',defaults={'name':'普通会员','rank':0})
        self.member=Member.objects.create(wechat_app_id='wx-d41-concurrency',wechat_openid='synthetic-A',grade=grade)
        self.other=Member.objects.create(wechat_app_id='wx-d41-concurrency',wechat_openid='synthetic-B',grade=grade)

    def campaign(self,total=1,limit=1):
        row=create_campaign({'code':'D41_RACE','title':'并发合成','kind':'CASH','minGoodsFen':0,'discountFen':100,
            'productIds':[],'redeemEligible':False,'validFrom':(timezone.now()-timedelta(days=1)).isoformat(),
            'validUntil':(timezone.now()+timedelta(days=1)).isoformat(),'totalQuantity':total,
            'selfClaimLimit':limit,'claimMode':'BOTH','issuanceEnabled':True})
        return publish_campaign(row.id,1)

    def race(self,functions):
        barrier=Barrier(len(functions))
        def runner(fn):
            connections.close_all()
            try:
                with connection.cursor() as cursor: cursor.execute("SET lock_timeout='5s'; SET statement_timeout='8s'")
                barrier.wait(timeout=5)
                try: return fn()
                except BenefitError as exc: return exc.code
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            tasks=[pool.submit(runner,fn) for fn in functions]
            return [task.result(timeout=15) for task in tasks]

    def test_only_one_member_gets_last_campaign_coupon(self):
        row=self.campaign()
        results=self.race([lambda:claim_coupon(self.member,row.id,uuid4()),lambda:claim_coupon(self.other,row.id,uuid4())])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('COUPON_QUOTA_EXHAUSTED',results)
        row.refresh_from_db(); self.assertEqual(row.issued_quantity,1)
        self.assertEqual((CouponIssuance.objects.count(),MemberCoupon.objects.count()),(1,1))

    def test_same_member_limit_is_not_exceeded_by_different_request_keys(self):
        row=self.campaign(2)
        results=self.race([lambda:claim_coupon(self.member,row.id,uuid4()),lambda:claim_coupon(self.member,row.id,uuid4())])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('COUPON_SELF_LIMIT',results)
        self.assertEqual(CouponAllocation.objects.get(member=self.member,campaign=row).self_count,1)

    def test_identical_claim_key_returns_exact_same_coupon(self):
        row=self.campaign(2); key=uuid4()
        results=self.race([lambda:claim_coupon(self.member,row.id,key),lambda:claim_coupon(self.member,row.id,key)])
        self.assertEqual(results[0],results[1])
        self.assertEqual(CouponIssuance.objects.count(),1)

    def test_claim_and_settlement_keep_member_lock_order_and_permanent_quota(self):
        row=self.campaign(3,2); coupon=claim_coupon(self.member,row.id,uuid4())['couponIds'][0]
        order_id=uuid4(); lines=[{'productId':str(uuid4()),'skuId':str(uuid4()),'fulfillmentKind':'SHIP','amountFen':200}]
        def settle():
            with transaction.atomic():
                reserve_benefits(self.member,order_id,lines,coupon_id=coupon)
                return consume_benefits(order_id)
        results=self.race([lambda:claim_coupon(self.member,row.id,uuid4()),settle])
        self.assertTrue(any(result is True for result in results))
        self.assertEqual(MemberCoupon.objects.get(pk=coupon).status,'USED')
        row.refresh_from_db(); self.assertEqual(row.issued_quantity,2)

    def test_authorization_revoked_while_waiting_for_campaign_lock_prevents_publish(self):
        import json,time
        from threading import Event
        from django.test import Client
        from accounts.models import AdminAccount,PermissionGroup,GroupPermission
        password='Synthetic D41 concurrent authorization 2026!'
        for mode in ['permission','session']:
            actor=AdminAccount.objects.create_user('d41-staff-'+mode,password,display_name='合成运营')
            group=PermissionGroup.objects.create(code='d41-'+mode,name='并发授权'+mode)
            actor.permission_groups.add(group)
            GroupPermission.objects.bulk_create([GroupPermission(group=group,code=code) for code in ['coupon.read','coupon.publish']])
            client=Client(enforce_csrf_checks=True); client.get('/api/v1/admin/auth/csrf')
            def post(path,body,**headers):
                return client.post(path,json.dumps(body),content_type='application/json',
                    HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value,**headers)
            self.assertEqual(post('/api/v1/admin/auth/login',{'loginName':actor.login_name,'password':password}).status_code,200)
            row=create_campaign({'code':'D41_REVOKE_'+mode,'title':'并发撤权','kind':'CASH','minGoodsFen':0,'discountFen':100,
                'productIds':[],'redeemEligible':False,'validFrom':(timezone.now()-timedelta(days=1)).isoformat(),
                'validUntil':(timezone.now()+timedelta(days=1)).isoformat(),'totalQuantity':1,
                'selfClaimLimit':1,'claimMode':'BOTH','issuanceEnabled':True})
            confirmed=post('/api/v1/admin/auth/confirm',{'action':'coupon.publish','password':password,
                'objectId':str(row.id),'revision':1})
            self.assertEqual(confirmed.status_code,200)
            token=confirmed.json()['data']['confirmationToken']; started=Event()
            def write():
                connections.close_all()
                try:
                    started.set()
                    return post('/api/v1/admin/coupon-campaigns/'+str(row.id)+'/publish',{'expectedRevision':1},
                                HTTP_IDEMPOTENCY_KEY=str(uuid4()),HTTP_X_ACTION_CONFIRMATION=token).status_code
                finally: connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    CouponCampaign.objects.select_for_update().get(pk=row.id)
                    future=pool.submit(write); self.assertTrue(started.wait(3))
                    blocked=False
                    for _ in range(200):
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT pg_stat_clear_snapshot()")
                            cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE '%%benefit_coupon_campaign%%')")
                            blocked=cursor.fetchone()[0]
                        if blocked: break
                        time.sleep(0.01)
                    self.assertTrue(blocked,'request did not reach campaign lock; completed='+str(future.done()))
                    if mode=='permission': GroupPermission.objects.filter(group=group,code='coupon.publish').delete()
                    else: AdminAccount.objects.filter(pk=actor.id).update(auth_version=actor.auth_version+1)
                self.assertEqual(future.result(timeout=10),403 if mode=='permission' else 401)
            row.refresh_from_db(); self.assertEqual(row.status,'DRAFT')
