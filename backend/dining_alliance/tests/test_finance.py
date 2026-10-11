from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.db import transaction, DatabaseError
from orders.tests import test_dining_checkout as fixture
from orders.service import submit_order
from orders.models import Order
from stores.models import StoreSkuProfitRule
from dining_alliance.models import DiningOrderSnapshot, DiningOrderLineSnapshot, StoreSkuPurchaseCost, RestaurantStaff, Withdrawal
from dining_alliance.finance import income_data, account_data, request_withdrawal, change_withdrawal, snapshot_order
from dining_alliance.access import DiningError


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class DiningFinanceTests(TestCase):
    setUp=fixture.DiningCheckoutTests.setUp
    _sku=fixture.DiningCheckoutTests._sku
    make_store=fixture.DiningCheckoutTests.make_store
    scene=fixture.DiningCheckoutTests.scene
    dining_quote=fixture.DiningCheckoutTests.dining_quote

    def order(self,override=None,cost=600):
        store,restaurant,table,session=self.scene()
        StoreSkuProfitRule.objects.create(sku=self.skus[0],unit_version=self.skus[0].current_unit,purchase_cost_fen=cost,platform_share_bps=1000)
        if override is not None:
            StoreSkuPurchaseCost.objects.create(store=store,sku=self.skus[0],unit_version=self.skus[0].current_unit,purchase_cost_fen=override)
        quote=self.dining_quote(session)
        data,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid4())
        order=Order.objects.get(pk=data['orderId'])
        RestaurantStaff.objects.create(restaurant=restaurant,member=self.member,permissions=['finance'])
        return order,restaurant,session

    @transaction.atomic
    def complete(self,order,days=16):
        from fulfillment.models import StoreDelivery, StoreDeliveryEvent
        Order.objects.filter(pk=order.pk).update(status='PAID',paid_at=timezone.now())
        order.refresh_from_db()
        when=timezone.now()-timedelta(days=days)
        row=StoreDelivery.objects.create(order=order,mode='DELIVERY',status='READY',nonce='a'*32,prepared_at=when)
        row.status='IN_TRANSIT';row.dispatched_at=when;row.save()
        row.status='COMPLETED';row.completed_at=when;row.save()
        for action in ('PREPARE','DISPATCH','COMPLETE'):
            StoreDeliveryEvent.objects.create(order=order,actor=self.member,action=action,request_key=uuid4(),request_digest='a'*64,note='测试交付证据')
        from payments.store_settlement import settle_order
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            settle_order(order.id)

    def test_new_snapshot_ignores_retired_override_and_preserves_frozen_versions(self):
        order,restaurant,session=self.order(override=500)
        snap=DiningOrderSnapshot.objects.get(order=order)
        row=DiningOrderLineSnapshot.objects.get(snapshot=snap)
        self.assertEqual(snap.commission_fen,160)
        self.assertEqual(row.purchase_cost_fen,600)
        self.assertEqual(row.cost_source,'GLOBAL_DEFAULT')
        StoreSkuPurchaseCost.objects.update(purchase_cost_fen=900)
        StoreSkuProfitRule.objects.update(purchase_cost_fen=800)
        snap.refresh_from_db();self.assertEqual(snap.commission_fen,160)
        from payments.models import StoreOrderLineFinance
        row.refresh_from_db();self.assertEqual(row.purchase_cost_fen,600)
        source=StoreOrderLineFinance.objects.get(line__order=order)
        self.assertEqual((source.purchase_cost_fen,source.cost_source),(600,'GLOBAL_DEFAULT'))
        with self.assertRaises(DatabaseError),transaction.atomic():
            StoreOrderLineFinance.objects.filter(pk=source.pk).update(purchase_cost_fen=1)
        with self.assertRaises(DiningError):snapshot_order(order,session,self.member,list(order.lines.all()))
        for model in [DiningOrderSnapshot,DiningOrderLineSnapshot]:
            with self.assertRaises(DatabaseError),transaction.atomic():model.objects.update(commission_fen=1)

    def test_global_default_quantity_and_no_negative_profit_release(self):
        order,restaurant,session=self.order(cost=1200)
        snap=DiningOrderSnapshot.objects.get(order=order)
        self.assertFalse(snap.configured)
        self.assertEqual(income_data(snap)['status'],'HELD')
        self.assertEqual(account_data(restaurant)['availableFen'],0)

    def test_estimated_booked_after_completion_mature_after_window(self):
        order,restaurant,session=self.order()
        snap=DiningOrderSnapshot.objects.get(order=order)
        self.assertEqual(income_data(snap)['status'],'ESTIMATED')
        self.complete(order,days=1)
        snap.order=order
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            row=income_data(snap)
            self.assertEqual(row['status'],'PENDING');self.assertIsNotNone(row['bookedAt'])
            from payments.models import StoreIncome
            with self.assertRaises(DatabaseError),transaction.atomic():
                StoreIncome.objects.filter(order=order).update(dining_commission_fen=1)
            future=timezone.now()+timedelta(days=20)
            self.assertEqual(income_data(snap,now=future)['status'],'PENDING')
            from payments.store_settlement import settle_order
            settle_order(order.id,now=future)
            self.assertEqual(income_data(snap,now=future)['status'],'AVAILABLE')
        from aftersales.models import OrderAfterSaleSnapshot
        with patch('aftersales.models.OrderAfterSaleSnapshot.objects.filter') as policy, patch('payments.access.applied_receipt',return_value=True):
            policy.return_value.first.return_value=None
            self.assertEqual(income_data(snap)['status'],'HELD')

    def test_withdrawal_idempotency_freeze_reject_and_approval(self):
        order,restaurant,session=self.order()
        self.complete(order)
        body={'amountFen':100,'requestKey':str(uuid4())}
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            before=account_data(restaurant)
            row,created=request_withdrawal(restaurant.id,self.member,body)
            again,repeated=request_withdrawal(restaurant.id,self.member,body)
            self.assertTrue(created);self.assertFalse(repeated);self.assertEqual(row.pk,again.pk)
            self.assertEqual(account_data(restaurant)['availableFen'],before['availableFen']-100)
            with self.assertRaises(DiningError):request_withdrawal(restaurant.id,self.member,{**body,'amountFen':50})
            with self.assertRaises(DiningError):request_withdrawal(restaurant.id,self.other,{'amountFen':10,'requestKey':str(uuid4())})
            with self.assertRaises(DiningError):request_withdrawal(restaurant.id,self.member,{'amountFen':1000,'requestKey':str(uuid4())})
            row=change_withdrawal(restaurant.id,row.pk,'reject',{'revision':1,'reason':'金额核对'})
            self.assertEqual(account_data(restaurant)['availableFen'],before['availableFen'])
            with self.assertRaises(DiningError):change_withdrawal(restaurant.id,row.pk,'approve',{'revision':2})
            row,_=request_withdrawal(restaurant.id,self.member,{'amountFen':100,'requestKey':str(uuid4())})
            row=change_withdrawal(restaurant.id,row.pk,'approve',{'revision':1})
            self.assertEqual(row.status,'APPROVED')
            self.assertFalse(account_data(restaurant)['payoutEnabled'])

    def test_aftersale_freezes_and_withdrawal_approval_rechecks(self):
        order,restaurant,session=self.order()
        self.complete(order)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            row,_=request_withdrawal(restaurant.id,self.member,{'amountFen':100,'requestKey':str(uuid4())})
            with patch('aftersales.models.AfterSaleCase.objects.filter') as cases:
                cases.return_value.exclude.return_value.exists.return_value=True
                self.assertEqual(account_data(restaurant)['availableFen'],0)
                with self.assertRaises(DiningError):change_withdrawal(restaurant.id,row.pk,'approve',{'revision':1})
                cases.return_value.exclude.assert_called_with(status__in=['REJECTED','WITHDRAWN'])
                cases.return_value.exclude.return_value.exists.return_value=False
                self.assertEqual(account_data(restaurant)['availableFen'],60)

    def test_reads_have_no_finance_writes_and_merchant_order_scrub(self):
        from dining_alliance.views import order_data
        from payments.models import StoreWalletEvent
        order,restaurant,session=self.order()
        before=StoreWalletEvent.objects.count()
        for _ in range(2):account_data(restaurant)
        self.assertEqual(StoreWalletEvent.objects.count(),before)
        result=order_data(DiningOrderSnapshot.objects.get(order=order))
        text=str(result)
        for key in ['sessionId','memberId','phone','couponId','pointsDiscountFen']:
            self.assertNotIn(key,text)


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class DiningWithdrawalConcurrencyTests(TransactionTestCase):
    def setUp(self):
        with transaction.atomic():
            DiningFinanceTests.setUp(self)
            from shipping.models import ShippingPolicy
            ShippingPolicy.objects.update_or_create(pk=1,defaults={'fee_fen':0})
    _sku=DiningFinanceTests._sku
    make_store=DiningFinanceTests.make_store
    scene=DiningFinanceTests.scene
    dining_quote=DiningFinanceTests.dining_quote
    order=DiningFinanceTests.order
    complete=DiningFinanceTests.complete

    def concurrent(self,operation):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        barrier=Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b=pool.submit(run),pool.submit(run)
            return a.result(timeout=20),b.result(timeout=20)

    def test_duplicate_concurrent_request_creates_once(self):
        order,restaurant,session=self.order();self.complete(order)
        body={'amountFen':100,'requestKey':str(uuid4())}
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            result=self.concurrent(lambda:request_withdrawal(restaurant.id,self.member,body))
        self.assertEqual(sum(created for _,created in result),1)
        self.assertEqual(Withdrawal.objects.count(),1)

    def test_concurrent_different_requests_cannot_overspend(self):
        order,restaurant,session=self.order();self.complete(order)
        def apply():
            try:return request_withdrawal(restaurant.id,self.member,{'amountFen':100,'requestKey':str(uuid4())})[1]
            except DiningError:return False
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            result=self.concurrent(apply)
        self.assertEqual(sum(result),1)
        self.assertEqual(Withdrawal.objects.count(),1)

    def test_withdrawal_evidence_amount_and_identity_are_immutable(self):
        order,restaurant,session=self.order();self.complete(order)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            row,_=request_withdrawal(restaurant.id,self.member,{'amountFen':100,'requestKey':str(uuid4())})
        with self.assertRaises(DatabaseError),transaction.atomic():
            Withdrawal.objects.filter(pk=row.pk).update(amount_fen=1)
        with self.assertRaises(DatabaseError),transaction.atomic():
            Withdrawal.objects.filter(pk=row.pk).delete()
