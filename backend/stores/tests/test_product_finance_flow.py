from django.test import TransactionTestCase, override_settings
from stores.tests.test_finance_flow import StoreFinanceFlowTests
from stores.models import StoreProductProfitRule, StoreSkuProfitRule
from payments.models import StoreOrderLineFinance
from payments.store_finance import order_split


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':True,'OFFLINE':True})
class ProductFinanceSnapshotTests(TransactionTestCase):
    setUp = StoreFinanceFlowTests.setUp
    _order = StoreFinanceFlowTests._order
    _evidence = StoreFinanceFlowTests._evidence
    _paid_order = StoreFinanceFlowTests._paid_order
    local_order = StoreFinanceFlowTests.local_order
    action = StoreFinanceFlowTests.action
    completed = StoreFinanceFlowTests.completed

    def test_product_ratio_overrides_legacy_sku_and_freezes_order(self):
        share = StoreProductProfitRule.objects.create(product=self.sku.product, platform_share_bps=3000)
        order, line = self.completed()
        snapshot = StoreOrderLineFinance.objects.get(line=line)
        self.assertEqual(snapshot.platform_share_bps,3000)
        before = order_split(order)
        StoreProductProfitRule.objects.filter(pk=share.pk).update(platform_share_bps=5000,revision=2)
        StoreSkuProfitRule.objects.filter(pk=self.rule.pk).update(purchase_cost_fen=0,revision=2)
        self.assertEqual(order_split(order),before)
