"""Real session/CSRF/bearer validation for scoped coupon operations."""
import json
from datetime import timedelta
from uuid import uuid4
from django.db import connection, transaction, DatabaseError
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from accounts.models import AdminAccount,PermissionGroup,GroupPermission,AuditLog
from benefits.models import CouponCampaign,CouponIssuance,MemberCoupon,CouponAllocation,CouponOperation
from benefits.coupon_operations import create_campaign,publish_campaign,claim_coupon,issue_coupons
from catalog.models import MemberGrade
from customers.models import Member,MemberSession

PASSWORD='Synthetic D41 test passphrase 2026!'
BASE='/api/v1/admin/coupon-campaigns'
APP='/api/v1/app/coupon-campaigns'


@override_settings(WECHAT_MINI_APP_ID='wx-d41-api')
class D41ApiTests(TestCase):
    def setUp(self):
        self.owner=AdminAccount.objects.create_user('d41-owner',PASSWORD,kind='OWNER',display_name='合成运营')
        self.client=self.login(self.owner)
        grade=MemberGrade.objects.get(code='normal')
        self.member=Member.objects.create(wechat_app_id='wx-d41-api',wechat_openid='private-d41-A',grade=grade)
        self.other=Member.objects.create(wechat_app_id='wx-d41-api',wechat_openid='private-d41-B',grade=grade)
        self.token=MemberSession.issue(self.member)[0]
        self.app=Client(enforce_csrf_checks=True)

    def login(self,actor):
        client=Client(enforce_csrf_checks=True); client.get('/api/v1/admin/auth/csrf')
        result=self.write(client,'post','/api/v1/admin/auth/login',{'loginName':actor.login_name,'password':PASSWORD})
        self.assertEqual(result.status_code,200,result.content)
        return client

    def write(self,client,verb,path,body,key=None,confirmation=None):
        headers={'HTTP_X_CSRFTOKEN':client.cookies['csrftoken'].value}
        if key: headers['HTTP_IDEMPOTENCY_KEY']=str(key)
        if confirmation: headers['HTTP_X_ACTION_CONFIRMATION']=confirmation
        return getattr(client,verb)(path,json.dumps(body),content_type='application/json',**headers)

    def body(self,**changes):
        return {'code':'D41','title':'合成活动','kind':'CASH','minGoodsFen':0,'discountFen':100,
            'productIds':[],'redeemEligible':False,'validFrom':(timezone.now()-timedelta(days=1)).isoformat(),
            'validUntil':(timezone.now()+timedelta(days=2)).isoformat(),'totalQuantity':4,
            'selfClaimLimit':1,'claimMode':'BOTH','issuanceEnabled':True,**changes}

    def campaign(self,**changes):
        campaign=create_campaign(self.body(**changes)); publish_campaign(campaign.id,1)
        return CouponCampaign.objects.get(pk=campaign.id)

    def confirm(self,campaign,action='coupon.publish',client=None,object_id=None,revision=None):
        client=client or self.client
        result=self.write(client,'post','/api/v1/admin/auth/confirm',{'action':action,'password':PASSWORD,
            'objectId':str(object_id or campaign.id),'revision':revision or campaign.revision})
        self.assertEqual(result.status_code,200,result.content)
        return result.json()['data']['confirmationToken']

    def claim(self,campaign,key=None,body=None,token=None):
        return self.app.post(APP+'/'+str(campaign.id)+'/claim',json.dumps({} if body is None else body),
            content_type='application/json',HTTP_AUTHORIZATION='Bearer '+(token or self.token),
            HTTP_IDEMPOTENCY_KEY=str(key or uuid4()))

    def staff(self,codes):
        actor=AdminAccount.objects.create_user('d41-staff',PASSWORD,display_name='合成只读')
        group=PermissionGroup.objects.create(code='d41',name='D41合成')
        actor.permission_groups.add(group)
        GroupPermission.objects.bulk_create([GroupPermission(group=group,code=code) for code in codes])
        return self.login(actor),group,actor

    def test_create_recovery_same_key_and_conflicting_body(self):
        body=self.body(); key=uuid4()
        first=self.write(self.client,'post',BASE,body,key)
        self.assertEqual(first.status_code,201,first.content)
        second=self.write(self.client,'post',BASE,body,key)
        self.assertEqual(first.json()['data'],second.json()['data'])
        recovery=self.client.get('/api/v1/admin/coupon-operations/'+str(key))
        self.assertEqual(recovery.json()['data'],{'status':'COMPLETED','result':first.json()['data']})
        conflict=self.write(self.client,'post',BASE,{**body,'title':'不同内容'},key)
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(CouponOperation.objects.count(),1)
        self.assertEqual(AuditLog.objects.filter(action_code='coupon.create').count(),1)

    def test_publish_requires_csrf_confirmation_correct_target_and_revision(self):
        campaign=create_campaign(self.body()); path=BASE+'/'+str(campaign.id)+'/publish'; body={'expectedRevision':1}
        self.assertEqual(self.client.post(path,json.dumps(body),content_type='application/json').status_code,403)
        self.assertEqual(self.write(self.client,'post',path,body,uuid4()).status_code,403)
        wrong=self.confirm(campaign,object_id=uuid4())
        self.assertEqual(self.write(self.client,'post',path,body,uuid4(),wrong).status_code,403)
        token=self.confirm(campaign); key=uuid4()
        result=self.write(self.client,'post',path,body,key,token)
        self.assertEqual(result.status_code,200,result.content)
        replay=self.write(self.client,'post',path,body,key,token)
        self.assertEqual(replay.json()['data'],result.json()['data'])
        self.assertEqual(result.json()['data']['status'],'PUBLISHED')

    def test_draft_edit_revision_and_published_terms_frozen(self):
        body=self.body(); campaign=create_campaign(body)
        path=BASE+'/'+str(campaign.id)
        self.assertEqual(self.write(self.client,'put',path,{**body,'expectedRevision':2},uuid4()).status_code,409)
        result=self.write(self.client,'put',path,{**body,'title':'新名称','expectedRevision':1},uuid4())
        self.assertEqual(result.status_code,200,result.content)
        publish_campaign(campaign.id,2)
        self.assertEqual(self.write(self.client,'put',path,{**body,'expectedRevision':3},uuid4()).status_code,409)

    def test_read_only_and_live_permission_revocation(self):
        reader,group,actor=self.staff(['coupon.read'])
        self.assertEqual(reader.get(BASE).status_code,200)
        self.assertEqual(self.write(reader,'post',BASE,self.body(),uuid4()).status_code,403)
        GroupPermission.objects.filter(group=group).delete()
        self.assertEqual(reader.get(BASE).status_code,403)

    def test_claim_own_recovery_limit_and_scoped_key(self):
        campaign=self.campaign(); key=uuid4()
        first=self.claim(campaign,key)
        self.assertEqual(first.status_code,201,first.content)
        self.assertEqual(self.claim(campaign,key).json()['data'],first.json()['data'])
        self.assertEqual(self.claim(campaign).status_code,409)
        route='/api/v1/app/member/coupon-claims/'+str(key)
        mine=self.app.get(route,HTTP_AUTHORIZATION='Bearer '+self.token).json()['data']
        self.assertEqual(mine['status'],'COMPLETED')
        token=MemberSession.issue(self.other)[0]
        self.assertEqual(self.app.get(route,HTTP_AUTHORIZATION='Bearer '+token).json()['data'],{'status':'NOT_FOUND'})
        self.assertNotIn('private-d41',str(mine))

    def test_claim_rejects_forged_member_and_revoked_or_foreign_app_token(self):
        campaign=self.campaign()
        self.assertEqual(self.claim(campaign,body={'memberId':str(self.other.id)}).status_code,400)
        MemberSession.objects.filter(member=self.member).update(revoked_at=timezone.now())
        self.assertEqual(self.claim(campaign).status_code,401)
        self.other.wechat_app_id='foreign-app'; self.other.save(update_fields=['wechat_app_id'])
        self.assertEqual(self.claim(campaign,token=MemberSession.issue(self.other)[0]).status_code,401)
        self.assertEqual(CouponIssuance.objects.count(),0)

    def test_claim_modes_pause_expiry_quota_and_legacy_are_closed(self):
        campaign=self.campaign(claimMode='ADMIN')
        self.assertEqual(self.claim(campaign).json()['error']['code'],'COUPON_CLAIM_MODE_INVALID')
        legacy=CouponCampaign.objects.create(code='OLD',title='旧活动',kind='CASH',discount_fen=100,
            valid_from=timezone.now()-timedelta(days=1),valid_until=timezone.now()+timedelta(days=1))
        self.assertEqual(self.claim(legacy).status_code,409)
        campaign=self.campaign(code='CAP',totalQuantity=1)
        self.assertEqual(self.claim(campaign).status_code,201)
        self.assertEqual(self.claim(campaign,token=MemberSession.issue(self.other)[0]).json()['error']['code'],'COUPON_QUOTA_EXHAUSTED')

    def test_manual_default_and_repeat_authorization_with_reason(self):
        campaign=self.campaign()
        client,group,actor=self.staff(['coupon.read','coupon.issue','member.read'])
        body={'expectedRevision':2,'memberId':str(self.member.id),'quantity':1,'reason':''}
        path=BASE+'/'+str(campaign.id)+'/issuances'
        token=self.confirm(campaign,'coupon.issue',client)
        self.assertEqual(self.write(client,'post',path,body,uuid4(),token).status_code,201)
        token=self.confirm(campaign,'coupon.issue',client)
        self.assertEqual(self.write(client,'post',path,body,uuid4(),token).status_code,403)
        GroupPermission.objects.create(group=group,code='coupon.issue.repeat')
        token=self.confirm(campaign,'coupon.issue',client)
        self.assertEqual(self.write(client,'post',path,body,uuid4(),token).status_code,403)
        body={**body,'reason':'补发核对'}; key=uuid4(); token=self.confirm(campaign,'coupon.issue',client)
        result=self.write(client,'post',path,body,key,token)
        self.assertEqual(result.status_code,201,result.content)
        GroupPermission.objects.filter(group=group,code='coupon.issue.repeat').delete()
        self.assertEqual(self.write(client,'post',path,body,key,token).status_code,403)
        self.assertEqual(client.get('/api/v1/admin/coupon-operations/'+str(key)).status_code,403)
        allocation=CouponAllocation.objects.get(member=self.member,campaign=campaign)
        self.assertEqual((allocation.self_count,allocation.admin_count),(0,2))
        self.assertEqual(self.claim(campaign).status_code,201)

    def test_manual_member_scope_and_disabled_are_rejected(self):
        campaign=self.campaign(); path=BASE+'/'+str(campaign.id)+'/issuances'
        self.member.enabled=False; self.member.save(update_fields=['enabled'])
        body={'expectedRevision':2,'memberId':str(self.member.id),'quantity':1,'reason':''}
        token=self.confirm(campaign,'coupon.issue')
        self.assertEqual(self.write(self.client,'post',path,body,uuid4(),token).status_code,409)
        self.assertEqual(CouponIssuance.objects.count(),0)

    def test_pause_requires_confirmation_and_leaves_issued_coupon_usable(self):
        campaign=self.campaign(); result=self.claim(campaign).json()['data']
        body={'expectedRevision':2,'issuanceEnabled':False}; path=BASE+'/'+str(campaign.id)+'/distribution'
        token=self.confirm(campaign)
        self.assertEqual(self.write(self.client,'post',path,body,uuid4(),token).status_code,200)
        self.assertEqual(self.claim(campaign).status_code,409)
        from benefits.service import quote_benefits
        lines=[{'productId':str(uuid4()),'skuId':str(uuid4()),'fulfillmentKind':'SHIP','amountFen':200}]
        self.assertEqual(quote_benefits(self.member,lines,coupon_id=result['couponIds'][0])['couponDiscountFen'],100)

    def test_coupon_state_expired_is_derived_and_reserved_used_preserved(self):
        campaign=self.campaign(); ids=[]
        for index in range(3): ids.extend(issue_coupons(self.member,campaign.id,uuid4(),1,'合成' if index else '',actor=self.owner,allow_repeat=True)['couponIds'])
        MemberCoupon.objects.filter(pk=ids[0]).update(restored_valid_until=timezone.now()-timedelta(seconds=1))
        MemberCoupon.objects.filter(pk=ids[1]).update(status='RESERVED',reserved_order_id=uuid4(),restored_valid_until=timezone.now()-timedelta(seconds=1))
        MemberCoupon.objects.filter(pk=ids[2]).update(status='USED',reserved_order_id=uuid4(),restored_valid_until=timezone.now()-timedelta(seconds=1))
        for state in ['EXPIRED','RESERVED','USED']:
            result=self.app.get('/api/v1/app/member/coupons',{'status':state},HTTP_AUTHORIZATION='Bearer '+self.token)
            self.assertEqual(result.json()['data']['pagination']['total'],1,result.content)
            self.assertEqual(result.json()['data']['items'][0]['status'],state)
            self.assertIn('no-store',result['Cache-Control'])
            self.assertNotIn('actor',result.content.decode())

    def test_pagination_validation_and_bounded_campaign_queries(self):
        for index in range(21): create_campaign(self.body(code='D41_'+str(index)))
        with CaptureQueriesContext(connection) as queries:
            result=self.client.get(BASE)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(len(result.json()['data']['items']),20)
        self.assertLess(len(queries),12)
        for query in [{'pageSize':101},{'page':0},{'q':'x'*101},{'status':'INVALID'},{'page':['1','2']}]:
            self.assertEqual(self.client.get(BASE,query).status_code,400)
        self.assertEqual(self.app.get('/api/v1/app/member/coupons',{'memberId':str(self.other.id)},HTTP_AUTHORIZATION='Bearer '+self.token).status_code,400)

    def test_validation_rejects_noninteger_bad_dates_product_scope_and_oversized_body(self):
        invalid=[{'discountFen':True},{'code':'编号'},{'selfClaimLimit':5},{'validFrom':'2026-09-27'},
            {'productIds':[str(uuid4())]},{'redeemEligible':'true'},{'claimMode':'OPEN'},{'minGoodsFen':100}]
        for change in invalid:
            result=self.write(self.client,'post',BASE,self.body(**change),uuid4())
            self.assertEqual(result.status_code,400,result.content)
        self.assertEqual(self.write(self.client,'post',BASE,{'oversized':'x'*33000},uuid4()).status_code,400)
        self.assertEqual(CouponCampaign.objects.count(),0)

    def test_database_freezes_published_terms_and_rejects_unproven_coupon(self):
        campaign=self.campaign()
        for change in [{'discount_fen':200},{'active':False},{'total_quantity':5}]:
            with self.assertRaises(DatabaseError),transaction.atomic(): CouponCampaign.objects.filter(pk=campaign.id).update(**change)
        with self.assertRaises(DatabaseError),transaction.atomic(): MemberCoupon.objects.create(member=self.member,campaign=campaign)
        result=claim_coupon(self.member,campaign.id,uuid4()); coupon=MemberCoupon.objects.get(pk=result['couponIds'][0])
        with self.assertRaises(DatabaseError),transaction.atomic(): MemberCoupon.objects.filter(pk=coupon.id).update(member=self.other)
        with self.assertRaises(DatabaseError),transaction.atomic(): MemberCoupon.objects.filter(pk=coupon.id).delete()
        with self.assertRaises(DatabaseError),transaction.atomic(): CouponIssuance.objects.all().update(reason='tamper')

    def test_sql_commit_checks_reject_counter_or_receipt_without_entitlement(self):
        campaign=self.campaign()
        with self.assertRaises(DatabaseError),transaction.atomic():
            CouponCampaign.objects.filter(pk=campaign.id).update(issued_quantity=1)
            with connection.cursor() as cursor: cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        with self.assertRaises(DatabaseError),transaction.atomic():
            CouponAllocation.objects.create(member=self.member,campaign=campaign,self_count=1)
            with connection.cursor() as cursor: cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')

    def test_details_and_recovery_reject_extra_query_and_cache_errors(self):
        campaign=self.campaign()
        for route in [BASE+'/'+str(campaign.id),'/api/v1/admin/coupon-operations/'+str(uuid4())]:
            result=self.client.get(route,{'unrecognized':'value'})
            self.assertEqual(result.status_code,400)
            self.assertIn('no-store',result['Cache-Control'])
            self.assertIn('Authorization',result['Vary'])
        result=self.app.get('/api/v1/app/member/coupon-claims/'+str(uuid4()),{'memberId':str(self.other.id)},HTTP_AUTHORIZATION='Bearer '+self.token)
        self.assertEqual(result.status_code,400)
        anonymous=self.app.get(APP)
        self.assertEqual(anonymous.status_code,401)
        self.assertIn('no-store',anonymous['Cache-Control'])

    def test_successful_scope_picker_uses_coupon_permissions_without_catalog_read(self):
        reader,group,actor=self.staff(['coupon.read','coupon.manage'])
        result=reader.get('/api/v1/admin/coupon-product-options',{'q':'synthetic'})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['pagination']['total'],0)
        self.assertEqual(reader.get('/api/v1/admin/coupon-product-options',{'q':'x'*101}).status_code,400)

    def test_claimable_listing_limit_and_member_coupons_are_private(self):
        campaign=self.campaign()
        claim_coupon(self.other,campaign.id,uuid4())
        result=self.app.get(APP,HTTP_AUTHORIZATION='Bearer '+self.token)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['items'][0]['selfClaimedCount'],0)
        self.assertTrue(result.json()['data']['items'][0]['canClaim'])
        claim_coupon(self.member,campaign.id,uuid4())
        result=self.app.get(APP,HTTP_AUTHORIZATION='Bearer '+self.token)
        self.assertEqual(result.json()['data']['items'][0]['selfClaimedCount'],1)
        self.assertFalse(result.json()['data']['items'][0]['canClaim'])
        result=self.app.get('/api/v1/app/member/coupons',HTTP_AUTHORIZATION='Bearer '+self.token)
        self.assertEqual(result.json()['data']['pagination']['total'],1)
        self.assertNotIn(str(self.other.id),result.content.decode())
        self.assertNotIn('requestKey',result.content.decode())

    def test_issuance_history_and_actor_recovery_do_not_cross_actors(self):
        campaign=self.campaign(); key=uuid4()
        body={'expectedRevision':2,'memberId':str(self.member.id),'quantity':1,'reason':''}
        result=self.write(self.client,'post',BASE+'/'+str(campaign.id)+'/issuances',body,key,self.confirm(campaign,'coupon.issue'))
        self.assertEqual(result.status_code,201,result.content)
        reader,group,actor=self.staff(['coupon.read'])
        recovery=reader.get('/api/v1/admin/coupon-operations/'+str(key))
        self.assertEqual(recovery.json()['data'],{'status':'NOT_FOUND'})
        history=reader.get(BASE+'/'+str(campaign.id)+'/issuances')
        self.assertEqual(history.json()['data']['pagination']['total'],1)
        self.assertNotIn(str(key),history.content.decode())
        self.assertNotIn('bodyHash',history.content.decode())

    def test_new_coupon_uses_product_scope_and_redeem_opt_in_at_checkout(self):
        from catalog.models import Category,Product
        from benefits.service import quote_benefits,BenefitError
        category=Category.objects.create(name='合成范围')
        product=Product.objects.create(category=category,product_no='D41_SCOPE',name='合成指定商品')
        campaign=self.campaign(productIds=[str(product.id)])
        coupon=claim_coupon(self.member,campaign.id,uuid4())['couponIds'][0]
        lines=[{'productId':str(product.id),'skuId':str(uuid4()),'fulfillmentKind':'SHIP','amountFen':200}]
        self.assertEqual(quote_benefits(self.member,lines,coupon_id=coupon)['couponDiscountFen'],100)
        with self.assertRaises(BenefitError): quote_benefits(self.member,[{**lines[0],'productId':str(uuid4())}],coupon_id=coupon)
        with self.assertRaises(BenefitError): quote_benefits(self.member,[{**lines[0],'fulfillmentKind':'REDEEM'}],coupon_id=coupon)
        campaign=self.campaign(code='D41_REDEEM',productIds=[str(product.id)],redeemEligible=True)
        coupon=claim_coupon(self.member,campaign.id,uuid4())['couponIds'][0]
        self.assertEqual(quote_benefits(self.member,[{**lines[0],'fulfillmentKind':'REDEEM'}],coupon_id=coupon)['couponDiscountFen'],100)

    def test_internal_issue_replay_denies_revoked_operator_auth_and_permissions(self):
        from benefits.service import BenefitError
        campaign=self.campaign(); client,group,actor=self.staff(['coupon.read','coupon.issue','member.read'])
        key=uuid4(); issue_coupons(self.member,campaign.id,key,actor=actor)
        GroupPermission.objects.filter(group=group,code='coupon.issue').delete()
        with self.assertRaises(BenefitError): issue_coupons(self.member,campaign.id,key,actor=actor)
        GroupPermission.objects.create(group=group,code='coupon.issue')
        AdminAccount.objects.filter(pk=actor.id).update(auth_version=actor.auth_version+1)
        with self.assertRaises(BenefitError): issue_coupons(self.member,campaign.id,key,actor=actor)

    def test_internal_repeat_replay_rechecks_current_repeat_permission(self):
        from benefits.service import BenefitError
        campaign=self.campaign(); client,group,actor=self.staff(['coupon.read','coupon.issue','member.read','coupon.issue.repeat'])
        key=uuid4(); issue_coupons(self.member,campaign.id,key,2,'合成授权多发',actor=actor,allow_repeat=True)
        GroupPermission.objects.filter(group=group,code='coupon.issue.repeat').delete()
        with self.assertRaises(BenefitError): issue_coupons(self.member,campaign.id,key,2,'合成授权多发',actor=actor,allow_repeat=True)
