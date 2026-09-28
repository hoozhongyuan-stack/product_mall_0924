from datetime import timedelta
from uuid import uuid4
from django.test import TestCase, override_settings
from django.utils import timezone
from catalog.models import MemberGrade
from customers.models import Member


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test')
class D41CouponTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(wechat_app_id='wx-payment-test', wechat_openid=str(uuid4()),
                                            grade=MemberGrade.objects.get(code='normal'))
        from accounts.models import AdminAccount
        self.actor=AdminAccount.objects.create_user('d41-owner','Testonly-Pass123!',kind='OWNER',display_name='测试员')
        self.body = {'code':'D41','title':'合成活动','kind':'CASH','minGoodsFen':0,'discountFen':100,
            'productIds':[],'redeemEligible':False,'validFrom':(timezone.now()-timedelta(days=1)).isoformat(),
            'validUntil':(timezone.now()+timedelta(days=2)).isoformat(),'totalQuantity':2,
            'selfClaimLimit':1,'claimMode':'BOTH','issuanceEnabled':True}

    def test_claim_replay_does_not_use_quota_twice(self):
        from benefits.coupon_operations import create_campaign, publish_campaign, claim_coupon
        campaign=create_campaign(self.body)
        publish_campaign(campaign.id,1)
        key=uuid4()
        first=claim_coupon(self.member,campaign.id,key)
        self.assertEqual(first,claim_coupon(self.member,campaign.id,key))
        campaign.refresh_from_db()
        self.assertEqual(campaign.issued_quantity,1)

    def test_manual_does_not_count_self_limit_and_pause_preserves_coupon(self):
        from benefits.coupon_operations import create_campaign,publish_campaign,claim_coupon,issue_coupons,set_distribution
        from benefits.service import quote_benefits
        campaign=create_campaign(self.body); publish_campaign(campaign.id,1)
        issue_coupons(self.member,campaign.id,uuid4(),1,'',allow_repeat=False,actor=self.actor)
        result=claim_coupon(self.member,campaign.id,uuid4())
        campaign.refresh_from_db(); set_distribution(campaign.id,campaign.revision,False)
        lines=[{'productId':str(uuid4()),'skuId':str(uuid4()),'fulfillmentKind':'SHIP','amountFen':200}]
        self.assertEqual(quote_benefits(self.member,lines,coupon_id=result['couponIds'][0])['couponDiscountFen'],100)
