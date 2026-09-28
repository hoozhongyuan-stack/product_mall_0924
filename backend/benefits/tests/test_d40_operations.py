from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.utils import timezone
from catalog.models import MemberGrade
from customers.models import Member
from benefits.models import PointsPolicy, PointsAccount, PointsEvent, BenefitLedger
from benefits.service import grant_points, quote_benefits


class D40OperationsTests(TestCase):
    def setUp(self):
        self.grade = MemberGrade.objects.get(code='normal')
        self.member = Member.objects.create(wechat_app_id='synthetic', wechat_openid=str(uuid4()), grade=self.grade)
        self.lines = [{'productId':str(uuid4()),'skuId':str(uuid4()),'fulfillmentKind':'SHIP','amountFen':10000}]

    def test_configured_deduction_uses_points_not_fen(self):
        PointsPolicy.objects.update_or_create(pk=1,defaults={'deduct_points':200,'deduct_fen':100,'max_percent':30})
        grant_points(self.member,1000,timezone.now()+timedelta(days=2),'synthetic-d40')
        result=quote_benefits(self.member,self.lines,points_to_use=400)
        self.assertEqual(result['pointsDiscountFen'],200)
        self.assertEqual(result['pointsToUse'],400)

    def test_no_zero_value_points_consumption(self):
        PointsPolicy.objects.update_or_create(pk=1,defaults={'deduct_points':200,'deduct_fen':100})
        grant_points(self.member,10,timezone.now()+timedelta(days=2),'synthetic-small')
        from benefits.service import BenefitError
        with self.assertRaises(BenefitError): quote_benefits(self.member,self.lines,points_to_use=1)

    def test_ledger_does_not_double_grant_lifecycle(self):
        from customers.operations import point_rows
        grant=grant_points(self.member,100,timezone.now()+timedelta(days=2),'earn:synthetic')
        BenefitLedger.objects.create(member=self.member,grant=grant,kind='EARN',amount=100,balance=100,source_ref='earn:synthetic')
        rows,total=point_rows(self.member.id,1,20)
        self.assertEqual(total,1)
        self.assertEqual(rows[0]['kind'],'EARN')
        self.assertNotIn('earn:synthetic',str(rows))

    def test_expired_pending_not_available_or_debt_until_debited(self):
        from customers.operations import points_summary
        grant_points(self.member,100,timezone.now()-timedelta(days=1),'synthetic-expired')
        result=points_summary([self.member.id])[self.member.id]
        self.assertEqual(result['settledPoints'],100)
        self.assertEqual(result['availablePoints'],0)
        self.assertEqual(result['expiredPendingPoints'],100)
        self.assertEqual(result['debtPoints'],0)

    def test_new_grade_policy_cannot_be_replaced_by_late_old_order(self):
        from django.db import transaction
        from customers.consumption import adjust_effective_spend_locked
        from customers.models import MemberConsumption
        gold=MemberGrade.objects.get(code='gold')
        old=[{'gradeId':str(self.grade.id),'rank':0,'minimumSpendFen':0},
             {'gradeId':str(gold.id),'rank':2,'minimumSpendFen':100}]
        new=[{'gradeId':str(self.grade.id),'rank':0,'minimumSpendFen':0},
             {'gradeId':str(gold.id),'rank':2,'minimumSpendFen':10000}]
        with transaction.atomic():
            adjust_effective_spend_locked(self.member.id,uuid4(),200,'new',new,policy_revision=3)
            adjust_effective_spend_locked(self.member.id,uuid4(),200,'late-old',old,policy_revision=1)
        self.member.refresh_from_db()
        self.assertEqual(self.member.grade_id,self.grade.id)
        self.assertEqual(MemberConsumption.objects.get(member=self.member).grade_policy_revision,3)
