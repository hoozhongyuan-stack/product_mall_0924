from uuid import uuid4
from django.test import TestCase
from django.db import transaction
from catalog.models import MemberGrade
from customers.models import Member, MemberConsumption
from customers.consumption import adjust_effective_spend_locked

class GradeRuleActivationTests(TestCase):
    def setUp(self):
        self.normal=MemberGrade.objects.get(code='normal')
        self.silver=MemberGrade.objects.get(code='silver')
        self.member=Member.objects.create(wechat_app_id='synthetic',wechat_openid=str(uuid4()),grade=self.normal)
        self.old=[{'gradeId':str(self.normal.id),'rank':1,'minimumSpendFen':0},{'gradeId':str(self.silver.id),'rank':2,'minimumSpendFen':50000}]
        self.new=[{**r,'minimumSpendFen':100000 if r['rank']==2 else 0} for r in self.old]
    def adjust(self,order,target,revision,rules):
        with transaction.atomic():
            return adjust_effective_spend_locked(self.member.id,order,target,'synthetic',rules,policy_revision=revision)
    def test_old_order_never_reactivates_old_thresholds(self):
        old,new=uuid4(),uuid4()
        self.adjust(old,60000,1,self.old)
        self.member.refresh_from_db();self.assertEqual(self.member.grade_id,self.silver.id)
        self.adjust(new,10000,2,self.new)
        self.member.refresh_from_db();self.assertEqual(self.member.grade_id,self.normal.id)
        self.adjust(old,50000,1,self.old)
        self.member.refresh_from_db();self.assertEqual(self.member.grade_id,self.normal.id)
        account=MemberConsumption.objects.get(member=self.member)
        self.assertEqual(account.grade_policy_revision,2)
        self.assertEqual(account.effective_spend_fen,60000)
    def test_zero_spend_pending_order_does_not_activate_rules(self):
        self.adjust(uuid4(),0,2,self.new)
        self.assertEqual(MemberConsumption.objects.get(member=self.member).grade_policy_revision,0)
    def test_new_revision_activates_on_positive_settlement_only_once(self):
        order=uuid4();self.adjust(order,60000,1,self.old)
        self.adjust(uuid4(),1000,3,self.new)
        self.adjust(order,60000,1,self.old)
        self.assertEqual(MemberConsumption.objects.get(member=self.member).grade_policy_revision,3)
    def test_completed_zero_cash_order_can_activate_but_pending_cannot(self):
        self.adjust(uuid4(),60000,1,self.old)
        with transaction.atomic():
            adjust_effective_spend_locked(self.member.id,uuid4(),0,'synthetic',self.new,
                policy_revision=2,activate_rules=True)
        self.member.refresh_from_db()
        self.assertEqual(self.member.grade_id,self.normal.id)
        self.assertEqual(MemberConsumption.objects.get(member=self.member).grade_policy_revision,2)
