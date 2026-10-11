"""Order ownership and fulfillment stay independently searchable and immutable."""
from uuid import uuid4
from django.test import TestCase, override_settings
from orders.tests import test_dining_checkout as fixture
from dining_alliance.tests import test_access_api as access_fixture
from orders.service import submit_order
from orders.models import Order
from orders.queries import list_orders, admin_order_data
from payments.service import PaymentError

@override_settings(WECHAT_MINI_APP_ID='wx-order-test', ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE': True})
class DiningOrderAdminTests(TestCase):
    setUp = fixture.DiningCheckoutTests.setUp
    _sku = fixture.DiningCheckoutTests._sku
    make_store = fixture.DiningCheckoutTests.make_store
    scene = fixture.DiningCheckoutTests.scene
    dining_quote = fixture.DiningCheckoutTests.dining_quote
    admin_client = access_fixture.DiningAccessTests.admin_client

    def order(self):
        store, restaurant, table, session = self.scene()
        data, _ = submit_order(self.member, {'quoteId': self.dining_quote(session)['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        return Order.objects.get(pk=data['orderId']), store, restaurant, table

    def test_source_restaurant_and_fulfillment_filters_intersect(self):
        order, store, restaurant, _ = self.order()
        params = {'source': 'DINING', 'restaurantId': str(restaurant.pk), 'storeId': str(store.pk)}
        result = self.admin_client().get('/api/v1/admin/orders', params)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['data']['total'], 1)
        self.assertEqual(list_orders({'source': 'REGULAR'})['total'], 0)
        self.assertEqual(list_orders({**params, 'restaurantId': str(uuid4())})['total'], 0)
        self.assertEqual(list_orders({**params, 'storeId': str(uuid4())})['total'], 0)
        for params in [{'source': 'UNKNOWN'}, {'restaurantId': 'bad'}]:
            with self.assertRaises(PaymentError): list_orders(params)

    def test_renaming_does_not_change_order_attribution(self):
        order, store, restaurant, table = self.order()
        original = store.name
        store.name = '仓库新名称'; store.save()
        restaurant.name = '餐饮店新名称'; restaurant.save()
        table.number = '新桌号'; table.save()
        row = list_orders({'source': 'DINING'})['items'][0]
        self.assertEqual(row['source'], 'DINING')
        self.assertEqual(row['storeName'], original)
        self.assertEqual(row['dining']['name'], '餐饮小馆')
        self.assertEqual(row['dining']['tableNumber'], '包间01')
        self.assertEqual(row['fulfillmentWarehouse']['id'], str(store.warehouse_id))
        self.assertEqual(admin_order_data(order)['fulfillmentWarehouse'], row['fulfillmentWarehouse'])
        detail = self.admin_client().get('/api/v1/admin/dining-alliance/orders/' + str(order.pk))
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()['data']['fulfillmentStore']['name'], original)

    def test_historical_store_stays_in_dining_filter_choices(self):
        order, store, restaurant, _ = self.order()
        other = self.make_store('OTHER', stock=2)
        restaurant.assigned_store = other; restaurant.save()
        result = self.admin_client().get('/api/v1/admin/dining-alliance/order-stores')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual({row['id'] for row in result.json()['data']['items']}, {str(store.pk), str(other.pk)})
