from uuid import uuid4

from django.test import TestCase, override_settings

from inventory.models import InventoryBalance, Warehouse
from checkout.service import create_quote, QuoteValidationError
from orders.service import submit_order, OrderError
from orders.models import Order
from orders.tests import test_order_flow as fixture


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class StoreCheckoutTests(TestCase):
    setUp = fixture.OrderFlowTests.setUp
    _sku = fixture.OrderFlowTests._sku

    def make_store(self, code='STORE', stock=4):
        from stores.models import Store, StoreProduct
        self.product.fulfillment_kind = 'SHIP'
        self.product.save(update_fields=['fulfillment_kind'])
        warehouse = Warehouse.objects.create(code=code, name=code)
        store = Store.objects.create(name=code, warehouse=warehouse, supported_modes=['PICKUP', 'DELIVERY', 'EXPRESS'],
                                     latitude='28.200000', longitude='112.900000')
        StoreProduct.objects.create(store=store, product=self.product, on_sale=True)
        InventoryBalance.objects.create(warehouse=warehouse, sku=self.skus[0], on_hand_base_units=stock)
        return store

    def quote(self, store, mode='PICKUP', **extra):
        return create_quote({'items': [{'skuId': str(self.skus[0].id), 'quantity': 2}],
                             'storeId': str(store.id), 'deliveryMode': mode, **extra}, self.member)

    def test_pickup_reserves_selected_store_without_address_or_express_fee(self):
        store = self.make_store()
        quote = self.quote(store)
        self.assertTrue(quote['ready'])
        self.assertFalse(quote['addressRequired'])
        self.assertEqual(quote['shippingFeeFen'], 0)
        data, _ = submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        order = Order.objects.get(pk=data['orderId'])
        self.assertEqual(order.store_id, store.id)
        self.assertEqual(order.lines.get().warehouse_id, store.warehouse_id)
        self.assertEqual(data['storeName'], store.name)
        self.assertEqual(InventoryBalance.objects.get(warehouse=store.warehouse, sku=self.skus[0]).reserved_base_units, 2)
        self.assertEqual(InventoryBalance.objects.get(warehouse=self.warehouse, sku=self.skus[0]).reserved_base_units, 0)

    def test_sold_out_store_does_not_use_default_or_other_store_stock(self):
        store = self.make_store(stock=1)
        self.make_store('OTHER', 10)
        quote = self.quote(store)
        self.assertFalse(quote['ready'])
        self.assertEqual(quote['lines'][0]['status'], 'OUT_OF_STOCK')

    def test_down_shelf_after_quote_cannot_submit(self):
        from stores.models import StoreProduct
        store = self.make_store()
        quote = self.quote(store)
        StoreProduct.objects.filter(store=store).update(on_sale=False)
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        self.assertEqual(Order.objects.count(), 0)

    def test_store_policy_change_requires_new_quote(self):
        store = self.make_store()
        quote = self.quote(store)
        store.accepting_orders = False
        store.save(update_fields=['accepting_orders'])
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())

    def test_delivery_requires_configured_fee_radius_and_address_location(self):
        store = self.make_store()
        with self.assertRaises(QuoteValidationError):
            self.quote(store, 'DELIVERY')

    def test_store_context_rejects_rights_goods_and_invalid_mode(self):
        store = self.make_store()
        self.product.fulfillment_kind = 'REDEEM'
        self.product.save(update_fields=['fulfillment_kind'])
        self.assertFalse(self.quote(store)['ready'])
        with self.assertRaises(QuoteValidationError):
            self.quote(store, 'INVALID')

    def delivery_address(self, latitude='28.200100'):
        from customers.models import CustomerAddress
        return CustomerAddress.objects.create(member=self.member, recipient_name='张先生', phone='13800000000',
            province='湖南', city='长沙', district='岳麓区', detail='测试路 1 号', latitude=latitude, longitude='112.900000')

    def test_delivery_fee_and_range_are_verified_again_before_order(self):
        store = self.make_store()
        store.delivery_fee_fen, store.delivery_radius_meters = 600, 3000
        store.save()
        address = self.delivery_address()
        quote = self.quote(store, 'DELIVERY', addressId=str(address.id))
        self.assertEqual(quote['shippingFeeFen'], 600)
        address.latitude = '29.200000'
        address.save()
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        pending = self.quote(store, 'DELIVERY', addressId=str(address.id))
        self.assertTrue(pending['addressRequired'])
        self.assertTrue(pending['deliveryEligibilityPending'])

    def test_delivery_only_store_can_quote_goods_before_choosing_address_but_cannot_submit(self):
        store = self.make_store()
        store.delivery_fee_fen, store.delivery_radius_meters = 600, 3000
        store.supported_modes = ['DELIVERY']
        store.save()
        quote = self.quote(store, 'DELIVERY')
        self.assertTrue(quote['ready'])
        self.assertTrue(quote['addressRequired'])
        self.assertFalse(quote['orderSubmissionAvailable'])
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        address = self.delivery_address()
        eligible = self.quote(store, 'DELIVERY', addressId=str(address.id))
        self.assertFalse(eligible['addressRequired'])
        self.assertFalse(eligible['deliveryEligibilityPending'])
        data, _ = submit_order(self.member, {'quoteId': eligible['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        self.assertEqual(data['deliveryMode'], 'DELIVERY')

    def test_express_needs_shipping_address(self):
        store = self.make_store()
        quote = self.quote(store, 'EXPRESS')
        self.assertEqual(quote['shippingFeeFen'], 1000)
        self.assertTrue(quote['addressRequired'])
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())

    def test_different_store_cannot_reuse_same_order_request_key(self):
        store = self.make_store()
        other = self.make_store('OTHER', 10)
        key = uuid4()
        quote = self.quote(store)
        data, duplicate = submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, key)
        repeated, duplicate = submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, key)
        self.assertTrue(duplicate)
        self.assertEqual(repeated['orderId'], data['orderId'])
        second = self.quote(other)
        with self.assertRaises(OrderError) as error:
            submit_order(self.member, {'quoteId': second['quoteId'], 'paymentMethod': 'OFFLINE'}, key)
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_order_store_snapshot_is_database_immutable(self):
        from django.db import DatabaseError, transaction
        store = self.make_store()
        quote = self.quote(store)
        data, _ = submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        with self.assertRaises(DatabaseError), transaction.atomic():
            Order.objects.filter(pk=data['orderId']).update(store_snapshot={'name': 'Changed'})

    def test_public_catalog_uses_store_stock_and_hides_down_shelf(self):
        from stores.models import StoreProduct
        store = self.make_store(stock=1)
        result = self.client.get(f'/api/v1/app/products/{self.product.id}', {'storeId': str(store.id)})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['skus'][0]['availableQuantity'], 1)
        listing = StoreProduct.objects.get(store=store)
        listing.on_sale = False
        listing.save()
        self.assertEqual(self.client.get(f'/api/v1/app/products/{self.product.id}', {'storeId': str(store.id)}).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/app/products', {'storeId': str(store.id)}).json()['data']['rows'], [])
        from catalog.page_products import hydrate_products
        self.assertEqual(hydrate_products({'source': 'MANUAL', 'productIds': [str(self.product.id)], 'limit': 6}, store=store), [])

    def test_explicit_unknown_store_never_falls_back_to_legacy_stock(self):
        from checkout.store_context import store_context
        for body in ({'deliveryMode': 'PICKUP'}, {'storeId': 'bad', 'deliveryMode': 'PICKUP'},
                     {'storeId': None, 'deliveryMode': 'EXPRESS'}, {'storeId': str(uuid4()), 'deliveryMode': 'PICKUP'}):
            with self.assertRaises(ValueError):
                store_context(body, self.member, None)
        self.assertEqual(self.client.get('/api/v1/app/products', {'storeId': ''}).status_code, 400)
