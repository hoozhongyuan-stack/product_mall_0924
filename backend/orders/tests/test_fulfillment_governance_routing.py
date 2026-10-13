from uuid import uuid4

from django.test import TestCase, override_settings
from checkout.service import create_quote
from inventory.models import InventoryBalance
from orders.models import Order
from orders.service import submit_order, OrderError
from orders.tests.test_store_checkout import StoreCheckoutTests


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class FulfillmentGovernanceRoutingTests(TestCase):
    setUp = StoreCheckoutTests.setUp
    _sku = StoreCheckoutTests._sku
    make_store = StoreCheckoutTests.make_store
    delivery_address = StoreCheckoutTests.delivery_address

    def quote(self, store=None, mode='EXPRESS', **extra):
        body = {'items': [{'skuId': str(self.skus[0].id), 'quantity': 2}], 'deliveryMode': mode, **extra}
        if store:
            body['storeId'] = str(store.id)
        return create_quote(body, self.member)

    def test_express_uses_platform_stock_even_from_sold_out_store(self):
        store = self.make_store(stock=0)
        quote = self.quote(store, addressId=str(self.delivery_address().id))
        self.assertTrue(quote['ready'])
        data, _ = submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        order = Order.objects.get(pk=data['orderId'])
        self.assertIsNone(order.store_id)
        self.assertEqual(order.lines.get().warehouse_id, self.warehouse.id)
        self.assertEqual(data['fulfillmentOwner'], 'PLATFORM')
        self.assertEqual(data['sourceStore']['storeId'], str(store.id))
        self.assertEqual(InventoryBalance.objects.get(warehouse=store.warehouse, sku=self.skus[0]).reserved_base_units, 0)

    def test_platform_express_needs_no_front_warehouse(self):
        self.product.fulfillment_kind = 'SHIP'
        self.product.save(update_fields=['fulfillment_kind'])
        quote = self.quote(addressId=str(self.delivery_address().id))
        self.assertTrue(quote['orderSubmissionAvailable'])

    def test_order_line_freezes_redemption_scope_and_detects_stale_quote(self):
        store = self.make_store()
        self.product.fulfillment_kind = 'REDEEM'
        self.product.save(update_fields=['fulfillment_kind'])
        self.product.redeem_scope = 'BOTH'
        self.product.redeem_store_ids = [str(store.id)]
        self.product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        quote = self.quote(mode='REDEEM')
        self.product.redeem_scope = 'PLATFORM'
        self.product.redeem_store_ids = []
        self.product.save(update_fields=['redeem_scope', 'redeem_store_ids'])
        with self.assertRaises(OrderError) as caught:
            submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        self.assertEqual(caught.exception.code, 'REDEEM_SCOPE_CHANGED')

    def test_local_mixed_order_cannot_charge_virtual_to_store_pool(self):
        store = self.make_store()
        from catalog.models import Product, Sku, SkuUnitVersion
        from django.utils import timezone
        product = Product.objects.create(product_no='RIGHTS', name='权益', category=self.product.category,
            fulfillment_kind='REDEEM', status='ON_SALE', main_image=self.product.main_image,
            redeem_valid_until=timezone.localdate(), ever_on_sale=True)
        sku = Sku.objects.create(product=product, sku_code='RIGHTS', spec_key='single', sale_status='ON_SALE', list_price_fen=100)
        unit = SkuUnitVersion.objects.create(sku=sku, sale_unit='次', base_unit='次', ratio=1)
        sku.current_unit = unit
        sku.save(update_fields=['current_unit'])
        InventoryBalance.objects.create(warehouse=self.warehouse, sku=sku, on_hand_base_units=5)
        quote = create_quote({'items': [{'skuId': str(self.skus[0].id), 'quantity': 1}, {'skuId': str(sku.id), 'quantity': 1}],
            'storeId': str(store.id), 'deliveryMode': 'PICKUP'}, self.member)
        self.assertFalse(quote['ready'])
        self.assertEqual(next(row for row in quote['lines'] if row['skuId']==str(sku.id))['status'], 'LOCAL_MIXED_NOT_SUPPORTED')
