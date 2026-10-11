"""Warehouse-funded dining commission conserves money and freezes both parties."""
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.db import transaction, DatabaseError
from django.utils import timezone
from dining_alliance.tests import test_finance as fixture
from dining_alliance.models import DiningOrderSnapshot, DiningOrderLineSnapshot, ProductCommissionRule
from dining_alliance.finance import income_data, account_data
from payments.models import StoreIncome, StoreWallet, StoreWalletEvent, StoreOrderLineFinance
from payments.store_finance import order_split
from payments.store_settlement import settle_order
from stores.access import StoreError


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class WarehouseFundingTests(TestCase):
    setUp=fixture.DiningFinanceTests.setUp
    _sku=fixture.DiningFinanceTests._sku
    make_store=fixture.DiningFinanceTests.make_store
    scene=fixture.DiningFinanceTests.scene
    dining_quote=fixture.DiningFinanceTests.dining_quote
    order=fixture.DiningFinanceTests.order
    complete=fixture.DiningFinanceTests.complete

    def test_shared_store_cost_and_money_conserved(self):
        order,restaurant,session=self.order(override=900,cost=500)
        line=order.lines.get()
        self.assertEqual(StoreOrderLineFinance.objects.get(line=line).purchase_cost_fen,500)
        self.assertEqual(DiningOrderLineSnapshot.objects.get(line=line).purchase_cost_fen,500)
        snap=DiningOrderSnapshot.objects.get(order=order)
        self.assertEqual(snap.funding_source,'FULFILLMENT_STORE_PROFIT')
        amounts=order_split(order)
        self.assertEqual(amounts['platform_fen'],100)
        self.assertEqual(amounts['dining_commission_fen'],200)
        self.assertEqual(amounts['store_fen'],1700)
        self.assertEqual(sum(amounts[k] for k in ('store_fen','platform_fen','freight_fen','dining_commission_fen')),amounts['paid_fen'])
        self.complete(order)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            row=settle_order(order.id)
            self.assertEqual(row.status,'SETTLED')
            self.assertEqual(row.dining_commission_fen,200)
            from payments.store_accounts import income_data as store_income_data
            dto=store_income_data(row)
            self.assertEqual((dto['diningCommissionFen'],dto['beforeDiningFen'],dto['storeFen']),(200,1900,1700))
            self.assertEqual(StoreWallet.objects.get(store=order.store).available_fen,1700)
            self.assertEqual(account_data(restaurant)['availableFen'],200)
            self.assertEqual(StoreWalletEvent.objects.get(income=row).amount_fen,1700)
            settle_order(order.id)
            self.assertEqual(StoreWalletEvent.objects.filter(income=row).count(),1)
        with self.assertRaises(DatabaseError),transaction.atomic():
            StoreIncome.objects.filter(pk=row.pk).update(dining_commission_fen=0)

    def test_commission_cannot_consume_warehouse_cost_principal(self):
        original=self.scene
        def high_ratio_scene(*args,**kwargs):
            result=original(*args,**kwargs)
            ProductCommissionRule.objects.filter(product=self.product).update(commission_bps=9500)
            return result
        self.scene=high_ratio_scene
        order,restaurant,session=self.order()
        with self.assertRaises(StoreError):order_split(order)
        self.complete(order)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            row=settle_order(order.id)
            self.assertEqual(row.status,'HELD')
            self.assertIn('利润',row.reason)
            self.assertEqual(account_data(restaurant)['availableFen'],0)
            self.assertEqual(income_data(DiningOrderSnapshot.objects.get(order=order))['status'],'HELD')
        self.assertFalse(StoreWallet.objects.filter(store=order.store).exists())

    def test_maturity_without_warehouse_net_settlement_is_not_withdrawable(self):
        order,restaurant,session=self.order()
        self.complete(order)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            snapshot=DiningOrderSnapshot.objects.get(order=order)
            # Simulate scanner not having processed a mature order yet.
            with patch('payments.models.StoreIncome.objects.filter') as incomes:
                incomes.return_value.first.return_value=None
                self.assertEqual(income_data(snapshot)['status'],'PENDING')
                self.assertEqual(account_data(restaurant)['availableFen'],0)

    def test_freight_preserves_existing_platform_basis_and_money(self):
        original=self.scene
        def freight_scene(*args,**kwargs):
            from stores.models import StoreProductProfitRule
            result=original(*args,**kwargs)
            store=result[0];store.delivery_fee_fen=200;store.revision+=1;store.save()
            StoreProductProfitRule.objects.create(product=self.product,platform_share_bps=2000)
            from dining_alliance.service import scan
            return (*result[:3],scan(self.member,result[2].code)['sessionId'])
        self.scene=freight_scene
        order,restaurant,session=self.order()
        amounts=order_split(order)
        # Twice the confirmed 100/60/20% example, including 10 shipping.
        self.assertEqual((amounts['paid_fen'],amounts['platform_fen'],amounts['dining_commission_fen'],amounts['store_fen'],amounts['freight_fen']),(2200,200,160,1640,200))
        self.assertEqual(amounts['store_fen']+amounts['dining_commission_fen']+amounts['platform_fen']+amounts['freight_fen'],2200)
        self.complete(order)
        self.assertEqual(StoreWallet.objects.get(store=order.store).available_fen,1640)

    def test_high_freight_freezes_even_zero_dining_ratio(self):
        original=self.scene
        def freight_scene(*args,**kwargs):
            result=original(*args,**kwargs)
            store=result[0];store.delivery_fee_fen=10000;store.revision+=1;store.save()
            ProductCommissionRule.objects.filter(product=self.product).update(commission_bps=0)
            from dining_alliance.service import scan
            return (*result[:3],scan(self.member,result[2].code)['sessionId'])
        self.scene=freight_scene
        order,restaurant,session=self.order()
        with self.assertRaises(StoreError):order_split(order)
        self.complete(order)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            self.assertEqual(settle_order(order.id).status,'HELD')
            self.assertEqual(account_data(restaurant)['availableFen'],0)

    def test_legacy_funding_blank_holds_without_clawing_back_settled_warehouse(self):
        with patch('dining_alliance.finance.FUNDING_SOURCE',''):
            order,restaurant,session=self.order()
        self.complete(order)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            self.assertEqual(settle_order(order.id).status,'HELD')
            self.assertEqual(account_data(restaurant)['availableFen'],0)
        self.assertFalse(StoreWallet.objects.filter(store=order.store).exists())
        # A previously SETTLED old warehouse income is protected on every retry.
        # Create a separate fixture order without replaying the immutable old fact.
        from uuid import uuid4
        from orders.service import submit_order
        quote=self.dining_quote(session)
        with patch('dining_alliance.finance.FUNDING_SOURCE',''):
            result,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid4())
        from orders.models import Order
        other=Order.objects.get(pk=result['orderId'])
        self.complete(other)
        legacy=StoreIncome.objects.get(order=other)
        # Upgrade simulation: the old independent commission policy had settled
        # the warehouse at its old gross share, with no restaurant deduction.
        legacy.status='SETTLED';legacy.paid_fen=2000;legacy.cost_fen=1200;legacy.platform_fen=80;legacy.freight_fen=0;legacy.store_fen=1920;legacy.settled_at=timezone.now();legacy.save()
        wallet=StoreWallet.objects.create(store=order.store,available_fen=1920)
        StoreWalletEvent.objects.create(store=order.store,income=legacy,kind='SETTLE',amount_fen=1920)
        with patch('payments.access.applied_receipt',return_value=True),patch('payments.store_settlement.applied_receipt',return_value=True):
            self.assertEqual(settle_order(other.id).pk,legacy.pk)
            wallet.refresh_from_db();self.assertEqual(wallet.available_fen,1920)
            self.assertEqual(account_data(restaurant)['availableFen'],0)

from django.test import TransactionTestCase


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class ActualDiningSettlementTests(TransactionTestCase):
    setUp=fixture.DiningWithdrawalConcurrencyTests.setUp
    _sku=fixture.DiningFinanceTests._sku
    make_store=fixture.DiningFinanceTests.make_store
    scene=fixture.DiningFinanceTests.scene
    dining_quote=fixture.DiningFinanceTests.dining_quote
    order=fixture.DiningFinanceTests.order
    concurrent=fixture.DiningWithdrawalConcurrencyTests.concurrent

    def paid_delivery(self):
        from uuid import uuid4
        from payments.tests import test_payment_flow as payment
        from payments.service import record_verified_payment
        from stores.models import StoreStaff
        from fulfillment.store_service import operate, physical_handoff_fact
        from aftersales.models import OrderAfterSaleSnapshot
        order,restaurant,session=self.order(override=900,cost=500)
        evidence=payment.PaymentFlowTests._evidence(self,order,trade_no=uuid4().hex,event_id=uuid4().hex)
        self.assertEqual(record_verified_payment(evidence)['outcome'],'PAID')
        StoreStaff.objects.create(store=order.store,member=self.member,permissions=['orders'])
        for action in ('PREPARE','DISPATCH','COMPLETE'):
            order.refresh_from_db()
            operate(self.member,order.store_id,order.id,{'action':action,'expectedRevision':order.revision,'note':'已送达对应餐桌并确认交付'},uuid4())
        due=physical_handoff_fact(order).confirmed_at+timedelta(days=OrderAfterSaleSnapshot.objects.get(order=order).received_window_days,seconds=1)
        return order,restaurant,due

    def test_real_receipt_delivery_two_scanners_settle_net_once(self):
        order,restaurant,due=self.paid_delivery()
        results=self.concurrent(lambda:settle_order(order.id,now=due))
        self.assertTrue(all(row.status=='SETTLED' for row in results))
        self.assertEqual(StoreIncome.objects.filter(order=order).count(),1)
        self.assertEqual(StoreWalletEvent.objects.filter(income__order=order,kind='SETTLE').count(),1)
        self.assertEqual(StoreWallet.objects.get(store=order.store).available_fen,1700)
        with patch('dining_alliance.finance.timezone.now',return_value=due):
            self.assertEqual(account_data(restaurant)['availableFen'],200)
