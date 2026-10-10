"""Real checkout/payment/handoff/settlement integration without provider calls."""
import uuid
from datetime import timedelta
from django.test import TransactionTestCase,override_settings
from django.db import transaction,DatabaseError
from django.utils import timezone
from fulfillment.tests.test_store_delivery import StoreDeliveryFlowTests
from payments.models import StoreWallet,StoreIncome,StoreWalletEvent,StoreOrderLineFinance
from payments.store_settlement import settle_order,scan_settlements
from payments.store_finance import order_split
from stores.models import StoreSkuProfitRule


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class StoreFinanceFlowTests(TransactionTestCase):
    _order=StoreDeliveryFlowTests._order
    _evidence=StoreDeliveryFlowTests._evidence
    _paid_order=StoreDeliveryFlowTests._paid_order
    local_order=StoreDeliveryFlowTests.local_order
    action=StoreDeliveryFlowTests.action
    def setUp(self):
        StoreDeliveryFlowTests.setUp(self)
        self.rule=StoreSkuProfitRule.objects.create(sku=self.sku,unit_version=self.sku.current_unit,purchase_cost_fen=300,platform_share_bps=2000)

    def completed(self):
        from fulfillment.store_service import delivery_data
        order,line=self.local_order('PICKUP',paid=self.sku.list_price_fen>0)
        self.action(order,'PREPARE')
        self.action(order,'COMPLETE',pickupCode=delivery_data(order,include_code=True)['pickupCode'])
        order.refresh_from_db()
        return order,line

    def due(self,order):
        from fulfillment.store_service import physical_handoff_fact
        from aftersales.models import OrderAfterSaleSnapshot
        return physical_handoff_fact(order).confirmed_at+timedelta(days=OrderAfterSaleSnapshot.objects.get(order=order).received_window_days)

    def test_snapshot_immutable_and_policy_update_does_not_change_existing_sale(self):
        order,line=self.completed()
        snapshot=StoreOrderLineFinance.objects.get(line=line)
        self.assertTrue(snapshot.configured);self.assertEqual(snapshot.purchase_cost_fen,300)
        split=order_split(order)
        self.rule.purchase_cost_fen=1;self.rule.platform_share_bps=10000;self.rule.revision+=1;self.rule.save()
        self.assertEqual(order_split(order),split)
        with self.assertRaises(DatabaseError),transaction.atomic():StoreOrderLineFinance.objects.filter(pk=line.pk).update(purchase_cost_fen=1)

    def test_settle_only_after_window_exact_boundary_then_once(self):
        order,line=self.completed()
        deadline=self.due(order)
        self.assertEqual(settle_order(order.pk,deadline).status,'PENDING')
        self.assertFalse(StoreWallet.objects.filter(store=self.store).exists())
        row=settle_order(order.pk,deadline+timedelta(seconds=1))
        self.assertEqual(row.status,'SETTLED')
        wallet=StoreWallet.objects.get(store=self.store)
        self.assertEqual(wallet.available_fen,row.store_fen)
        settle_order(order.pk,deadline+timedelta(seconds=2))
        wallet.refresh_from_db();self.assertEqual(wallet.available_fen,row.store_fen)
        self.assertEqual(StoreWalletEvent.objects.filter(income=row).count(),1)
        with self.assertRaises(DatabaseError),transaction.atomic():StoreIncome.objects.filter(pk=row.pk).update(store_fen=0)

    def test_missing_policy_and_negative_profit_are_held(self):
        self.rule.delete()
        order,line=self.completed()
        row=settle_order(order.pk,self.due(order)+timedelta(seconds=1))
        self.assertEqual(row.status,'HELD');self.assertIn('未配置',row.reason)
        self.assertFalse(StoreWallet.objects.filter(store=self.store).exists())
        StoreSkuProfitRule.objects.create(sku=self.sku,unit_version=self.sku.current_unit,purchase_cost_fen=999999,platform_share_bps=2000)
        other,_=self.completed()
        self.assertEqual(settle_order(other.pk,self.due(other)+timedelta(seconds=1)).status,'HELD')

    def test_uncompleted_and_scan_cursor_advance_past_blocked_old_order(self):
        blocked,_=self.local_order('PICKUP')
        due,_=self.completed()
        from unittest.mock import patch
        now=self.due(due)+timedelta(seconds=1)
        with patch('payments.store_settlement.timezone.now',return_value=now):
            self.assertEqual(scan_settlements(1),[])
            rows=scan_settlements(1)
        self.assertEqual(rows[0].order_id,due.pk);self.assertEqual(rows[0].status,'SETTLED')
        self.assertFalse(StoreIncome.objects.filter(order=blocked).exists())

    def test_active_aftersale_blocks_then_withdrawal_allows_settlement(self):
        from aftersales.service import apply_case,withdraw_case
        order,line=self.completed()
        case=apply_case(self.member,line.pk,'RETURN_REFUND',1,'商品存在测试售后问题',uuid.uuid4())
        row=settle_order(order.pk,self.due(order)+timedelta(seconds=1))
        self.assertEqual(row.status,'PENDING');self.assertIn('售后处理中',row.reason)
        from aftersales.models import AfterSaleCase
        case=AfterSaleCase.objects.get(order_line=line)
        withdraw_case(case.pk,self.member,case.revision)
        self.assertEqual(settle_order(order.pk,self.due(order)+timedelta(seconds=1)).status,'SETTLED')

    def test_rule_bound_to_sale_unit_version(self):
        # A rule with no unit binding cannot be silently applied to a sale unit.
        self.rule.unit_version=None;self.rule.save()
        order,line=self.completed()
        self.assertFalse(StoreOrderLineFinance.objects.get(line=line).configured)
        self.assertEqual(settle_order(order.pk,self.due(order)+timedelta(seconds=1)).status,'HELD')

    def test_zero_price_paid_order_uses_paid_fact_and_holds_cost_loss(self):
        self.sku.list_price_fen=0;self.sku.save()
        self.rule.purchase_cost_fen=0;self.rule.save()
        order,line=self.completed()
        self.assertEqual(order.payable_fen,0)
        self.assertEqual(settle_order(order.pk,self.due(order)+timedelta(seconds=1)).status,'SETTLED')

    def test_account_summaries_omit_income_history_and_use_bounded_queries(self):
        from payments.store_accounts import account_summaries,account_data
        from stores.models import Store
        from inventory.models import Warehouse
        first,_=self.completed();second,_=self.completed()
        settle_order(first.pk,self.due(first)+timedelta(seconds=1))
        settle_order(second.pk,self.due(second)+timedelta(seconds=1))
        stores=[self.store]
        for index in range(15):
            stores.append(Store.objects.create(name=f'财务列表{index}',warehouse=Warehouse.objects.create(code=f'FINLIST-{index}',name='门店')))
        with self.assertNumQueries(2):summaries=account_summaries(stores)
        self.assertEqual(len(summaries),16)
        self.assertTrue(all(row['income']==[] and row['withdrawals']==[] for row in summaries))
        detail=account_data(self.store)
        self.assertEqual(len(detail['income']),2)
        self.assertEqual(summaries[0]['balance'],detail['balance'])
        self.assertEqual(account_data(self.store,include_history=False)['income'],[])
