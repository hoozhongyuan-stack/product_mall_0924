"""Restaurant scan orders keep the existing stock transaction and server address."""
from uuid import uuid4
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from orders.tests import test_store_checkout as store_fixture
from checkout.service import create_quote, QuoteValidationError
from orders.service import submit_order, OrderError
from orders.models import Order
from inventory.models import InventoryBalance


@override_settings(WECHAT_MINI_APP_ID='wx-order-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class DiningCheckoutTests(TestCase):
    setUp = store_fixture.StoreCheckoutTests.setUp
    _sku = store_fixture.StoreCheckoutTests._sku
    make_store = store_fixture.StoreCheckoutTests.make_store

    def scene(self, stock=12):
        from dining_alliance.models import Restaurant, DiningTable, RestaurantProduct, DiningSession, ProductCommissionRule
        store = self.make_store(stock=stock)
        store.delivery_fee_fen = 0
        store.delivery_radius_meters = 5000
        store.save()
        restaurant = Restaurant.objects.create(name='餐饮小馆', address='示例街 8 号', contact_name='店长', contact_phone='13800000000', latitude='28.200001', longitude='112.900001', assigned_store=store)
        table = DiningTable.objects.create(restaurant=restaurant, number='包间01')
        RestaurantProduct.objects.create(restaurant=restaurant, product=self.product, on_sale=True)
        ProductCommissionRule.objects.create(product=self.product, commission_bps=2000)
        from dining_alliance.service import scan
        result = scan(self.member, table.code)
        session_id = result['sessionId']
        return store, restaurant, table, session_id

    def dining_quote(self, session_id, qty=2, **extra):
        return create_quote({'items':[{'skuId':str(self.skus[0].id), 'quantity':qty}], 'diningSessionId':str(session_id), **extra}, self.member)

    def test_quote_without_member_address_and_order_has_frozen_table_target(self):
        from stores.models import StoreSkuProfitRule
        from dining_alliance.models import DiningOrderSnapshot
        store, restaurant, table, session_id = self.scene()
        StoreSkuProfitRule.objects.create(sku=self.skus[0], unit_version=self.skus[0].current_unit, purchase_cost_fen=600, platform_share_bps=1000)
        quote = self.dining_quote(session_id)
        self.assertTrue(quote['ready'])
        self.assertFalse(quote['addressRequired'])
        self.assertEqual(quote['dining']['tableNumber'], '包间01')
        data, duplicate = submit_order(self.member, {'quoteId':quote['quoteId'], 'paymentMethod':'OFFLINE'}, uuid4())
        self.assertFalse(duplicate)
        order = Order.objects.get(pk=data['orderId'])
        self.assertEqual(order.address_snapshot['tableNumber'], '包间01')
        self.assertIn('示例街 8 号', order.address_snapshot['detail'])
        self.assertEqual(order.store_id, store.id)
        self.assertEqual(order.delivery_mode, 'DELIVERY')
        self.assertEqual(order.lines.get().warehouse_id, store.warehouse_id)
        self.assertEqual(InventoryBalance.objects.get(warehouse=store.warehouse,sku=self.skus[0]).reserved_base_units, 2)
        self.assertEqual(InventoryBalance.objects.get(warehouse=self.warehouse,sku=self.skus[0]).reserved_base_units, 0)
        self.assertEqual(DiningOrderSnapshot.objects.get(order=order).commission_fen, 160)
        self.assertEqual(data['dining']['tableNumber'], '包间01')
        self.assertNotIn('sessionId', data['dining'])
        self.assertNotIn('sessionId', data['store']['dining'])
        table.number='新桌号';table.save()
        self.assertEqual(order.address_snapshot['tableNumber'], '包间01')

    def test_scan_context_cannot_be_forged_or_mixed_with_personal_address(self):
        store, _, _, session_id = self.scene()
        for extra in [{'storeId':str(store.id)}, {'deliveryMode':'PICKUP'}, {'addressId':str(uuid4())}]:
            with self.assertRaises(ValueError): self.dining_quote(session_id, **extra)
        with self.assertRaises(ValueError):
            create_quote({'items':[{'skuId':str(self.skus[0].id),'quantity':1}], 'diningSessionId':str(session_id)}, self.other)
        with self.assertRaises(ValueError):
            create_quote({'items':[{'skuId':str(self.skus[0].id),'quantity':1}], 'diningSessionId':str(session_id)}, None)

    def test_original_warehouse_shortage_never_switches(self):
        store, _, _, session_id = self.scene(stock=1)
        self.make_store('OTHER', stock=20)
        quote = self.dining_quote(session_id)
        self.assertFalse(quote['ready'])
        self.assertEqual(quote['lines'][0]['status'], 'OUT_OF_STOCK')
        self.assertEqual(quote['storeId'], str(store.id))

    def test_closed_scan_rejects_prior_quote(self):
        from dining_alliance.models import DiningSession
        _, _, _, session_id = self.scene()
        quote = self.dining_quote(session_id)
        DiningSession.objects.filter(pk=session_id).update(closed_at=timezone.now())
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId':quote['quoteId'], 'paymentMethod':'OFFLINE'}, uuid4())
        self.assertFalse(Order.objects.exists())

    def test_changed_table_revision_rejects_prior_quote(self):
        _, _, table, session_id = self.scene()
        quote = self.dining_quote(session_id)
        table.revision += 1
        table.save()
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId':quote['quoteId'], 'paymentMethod':'OFFLINE'}, uuid4())
        self.assertFalse(Order.objects.exists())

    def test_restaurant_down_shelf_after_quote_rejected_before_stock_reservation(self):
        from dining_alliance.models import RestaurantProduct
        store, restaurant, _, session_id = self.scene()
        quote = self.dining_quote(session_id)
        RestaurantProduct.objects.filter(restaurant=restaurant).update(on_sale=False)
        with self.assertRaises(OrderError):
            submit_order(self.member, {'quoteId':quote['quoteId'], 'paymentMethod':'OFFLINE'}, uuid4())
        self.assertFalse(Order.objects.exists())
        self.assertEqual(InventoryBalance.objects.get(warehouse=store.warehouse,sku=self.skus[0]).reserved_base_units, 0)

    def test_invalid_scan_identifiers_return_validation_error(self):
        import json
        for bad in ([], {}, 1, True, None, 'not-a-uuid'):
            response = self.client.post('/api/v1/app/checkout/quotes', data=json.dumps({
                'items':[{'skuId':str(self.skus[0].id),'quantity':1}], 'diningSessionId':bad}),
                content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {self.token}')
            self.assertEqual(response.status_code, 400)
