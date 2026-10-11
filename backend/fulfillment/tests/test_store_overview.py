"""Store dashboard counts match server-side queues and respect staff capabilities."""
from uuid import uuid4
from unittest.mock import patch
from django.test import Client, TransactionTestCase, override_settings
from customers.models import MemberSession
from orders.models import Order
from stores.models import StoreStaff
from fulfillment.tests import test_store_delivery as fixtures
from aftersales.service import apply_case, withdraw_case


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class StoreOverviewTests(TransactionTestCase):
    setUp = fixtures.StoreDeliveryFlowTests.setUp
    _order = fixtures.StoreDeliveryFlowTests._order
    _evidence = fixtures.StoreDeliveryFlowTests._evidence
    _paid_order = fixtures.StoreDeliveryFlowTests._paid_order
    local_order = fixtures.StoreDeliveryFlowTests.local_order
    action = fixtures.StoreDeliveryFlowTests.action

    def get(self, suffix='overview', query=None):
        token, _ = MemberSession.issue(self.member)
        return Client().get(f'/api/v1/app/store-center/stores/{self.store.id}/{suffix}', query or {}, HTTP_AUTHORIZATION='Bearer ' + token)

    def test_real_counts_match_each_queue_and_exceed_first_page(self):
        prep, _ = self.local_order('PICKUP')
        pickup, _ = self.local_order('PICKUP'); self.action(pickup, 'PREPARE')
        delivery, _ = self.local_order('DELIVERY'); self.action(delivery, 'PREPARE')
        shipment, _ = self.local_order('EXPRESS')
        values = {field.name: getattr(prep, field.name) for field in Order._meta.fields if field.name not in ('id', 'order_no', 'quote_id', 'created_at')}
        values.update(status='PENDING_PAYMENT', paid_at=None)
        Order.objects.bulk_create([Order(id=uuid4(), order_no=uuid4().hex, quote_id=uuid4(), **values) for _ in range(21)])
        result = self.get(); self.assertEqual(result.status_code, 200)
        stats = result.json()['data']['stats']
        self.assertEqual(stats, dict(waitingPreparation=1, waitingPickup=1, waitingDelivery=1, waitingShipment=1, totalOrders=25, activeAfterSales=0))
        for field, task in [('waitingPreparation', 'WAITING_PREPARATION'), ('waitingPickup', 'WAITING_PICKUP'), ('waitingDelivery', 'WAITING_DELIVERY'), ('waitingShipment', 'WAITING_SHIPMENT')]:
            self.assertEqual(self.get('orders', {'fulfillment': task}).json()['data']['total'], stats[field])
        orders = self.get('orders')
        self.assertEqual(len(orders.json()['data']['items']), 20)
        self.assertIn('no-store', orders['Cache-Control'])
        self.assertIn('Authorization', self.get('aftersales')['Vary'])
        self.assertIn('no-store', result['Cache-Control']); self.assertIn('Authorization', result['Vary'])
        self.assertNotIn('pickupCode', str(result.json()))

    def test_active_cases_and_blocked_fulfillment_share_queue_definition(self):
        order, line = self.local_order('PICKUP')
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '工作台统计回归', uuid4())
        self.assertEqual(self.get().json()['data']['stats']['waitingPreparation'], 0)
        self.assertEqual(self.get().json()['data']['stats']['activeAfterSales'], 1)
        self.assertEqual(self.get('aftersales', {'status': 'ACTIVE'}).json()['data']['total'], 1)
        withdraw_case(case.id, self.member, case.revision)
        self.assertEqual(self.get().json()['data']['stats']['activeAfterSales'], 0)
        self.assertEqual(self.get('aftersales', {'status': 'ACTIVE'}).json()['data']['total'], 0)
        self.assertEqual(self.get('aftersales').json()['data']['total'], 1)

    def test_no_order_permission_never_reads_sensitive_counts_and_disabled_staff_denied(self):
        row = StoreStaff.objects.get(store=self.store, member=self.member)
        row.permissions = ['products']; row.save(update_fields=['permissions'])
        with patch('fulfillment.store_overview.order_task_counts', side_effect=AssertionError('must not query orders')):
            result = self.get(); self.assertEqual(result.status_code, 200)
            self.assertIsNone(result.json()['data']['stats'])
        row.enabled = False; row.save(update_fields=['enabled'])
        self.assertEqual(self.get().status_code, 403)
        self.assertEqual(Client().get(f'/api/v1/app/store-center/stores/{self.store.id}/overview').status_code, 401)

    def test_queries_are_strict_and_another_store_cannot_leak(self):
        from stores.models import Store
        from inventory.models import Warehouse
        other = Store.objects.create(warehouse=Warehouse.objects.create(name='隔离仓', code=uuid4().hex), name='隔离仓', contact_name='测试', contact_phone='1', address='测试')
        token, _ = MemberSession.issue(self.member)
        self.assertEqual(Client().get(f'/api/v1/app/store-center/stores/{other.id}/overview', HTTP_AUTHORIZATION='Bearer ' + token).status_code, 403)
        self.local_order('PICKUP')
        StoreStaff.objects.create(store=other, member=self.member, permissions=['orders'])
        empty = Client().get(f'/api/v1/app/store-center/stores/{other.id}/overview', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(set(empty.json()['data']['stats'].values()), {0})
        self.assertEqual(self.get(query={'page': '1'}).status_code, 400)
        self.assertEqual(Client().post(f'/api/v1/app/store-center/stores/{self.store.id}/overview').status_code, 405)
        for suffix, query in [('orders', {'fulfillment': ['WAITING_PICKUP', 'WAITING_DELIVERY']}), ('orders', {'status': ['PAID', 'CLOSED']}), ('orders', {'bogus': '1'}), ('aftersales', {'status': 'UNKNOWN'}), ('aftersales', {'status': ['ACTIVE', 'ACTIVE']})]:
            self.assertEqual(self.get(suffix, query).status_code, 400)
