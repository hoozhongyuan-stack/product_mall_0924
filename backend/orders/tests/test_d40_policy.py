import uuid
from datetime import timedelta
from django.db import transaction, DatabaseError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from fulfillment.tests import test_flow
from benefits.models import PointsPolicy, OrderBenefitSnapshot, PointsAccount
from benefits.service import grant_points
from benefits.lifecycle import reconcile_order_benefits_locked
from checkout.service import create_quote
from orders.service import submit_order, OrderError
from orders.models import Order

@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class D40PolicyOrdersTests(TransactionTestCase):
    def setUp(self):
        self.f=test_flow.FulfillmentFlowTests();self.f.setUp()
    def test_policy_change_invalidates_quote_and_keeps_old_order_snapshot(self):
        quote=create_quote({'items':[{'skuId':str(self.f.sku.id),'quantity':2}]},self.f.member)
        PointsPolicy.objects.update_or_create(pk=1,defaults={'revision':2,'earn_points':2})
        with self.assertRaises(OrderError) as caught:
            submit_order(self.f.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid.uuid4())
        self.assertEqual(caught.exception.code,'BENEFIT_POLICY_CHANGED')
        order=self.f._order()
        self.assertEqual(order.benefit_policy_snapshot['revision'],2)
        PointsPolicy.objects.filter(pk=1).update(revision=3,earn_points=5)
        order.refresh_from_db();self.assertEqual(order.benefit_policy_snapshot['earnPoints'],2)
        with self.assertRaises(DatabaseError),transaction.atomic():
            Order.objects.filter(pk=order.id).update(benefit_policy_snapshot={})
    def test_deduction_money_and_refunded_point_count_remain_distinct(self):
        PointsPolicy.objects.update_or_create(pk=1,defaults={'deduct_points':200,'deduct_fen':100})
        grant_points(self.f.member,1000,timezone.now()+timedelta(days=30),'synthetic-d40-order')
        order=self.f._order(points=400)
        self.assertEqual(order.points_discount_fen,200)
        self.assertEqual(PointsAccount.objects.get(member=self.f.member).frozen_points,400)
        from payments.service import record_verified_payment
        record_verified_payment(self.f._evidence(order))
        order.refresh_from_db();line=order.lines.get()
        snapshot=OrderBenefitSnapshot.objects.get(order_id=order.id)
        self.assertEqual(snapshot.line_snapshot[0]['points'],400)
        with transaction.atomic():
            reconcile_order_benefits_locked(order,fulfilled=True,active_aftersale=False,refunds=[])
        with transaction.atomic():
            result=reconcile_order_benefits_locked(order,fulfilled=True,active_aftersale=False,
                refunds=[{'lineId':str(line.id),'quantity':1,'amountFen':900}])
        self.assertEqual(result['returnedPoints'],200)
    def test_database_rejects_null_conversion_and_snapshot_rewrite(self):
        order=self.f._order()
        values={field.attname:getattr(order,field.attname) for field in Order._meta.concrete_fields if not field.primary_key}
        values.update(order_no='synthetic-'+uuid.uuid4().hex,benefit_policy_snapshot={'deductPoints':None,'deductFen':None})
        with self.assertRaises(DatabaseError),transaction.atomic():Order.objects.create(**values)
