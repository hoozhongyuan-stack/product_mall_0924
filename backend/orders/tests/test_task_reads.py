"""Read-only task filters use the same persisted facts as fulfillment actions."""
from datetime import timedelta
import json
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from aftersales.service import apply_case, review_case, withdraw_case
from benefits.service import grant_points
from catalog.models import Product, Sku, SkuUnitVersion
from checkout.service import create_quote
from customers.models import CustomerAddress, Member, MemberSession
from fulfillment.models import Carrier, RedeemVoucher
from fulfillment.service import confirm_receipt, redeem, ship_order
from inventory.models import InventoryBalance
from orders.models import Order
from orders.queries import list_orders
from orders.service import cancel_order, submit_order
from payments.refunds import prepare_refund, record_verified_refund
from payments.service import record_verified_payment
from payments.tests import test_refund_flow as fixtures
from points_exchange.tests import test_d5_exchange as exchange_fixtures


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', EXCHANGE_ORDER_ENABLED=True,
                   ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE': True, 'WECHAT': True})
class OrderTaskReadTests(TransactionTestCase):
    setUp = fixtures.RefundFlowTests.setUp
    _order = fixtures.RefundFlowTests._order
    _paid_order = fixtures.RefundFlowTests._paid_order
    _evidence = fixtures.RefundFlowTests._evidence
    refund_evidence = fixtures.RefundFlowTests.refund_evidence

    def ids(self, task, **params):
        return {row['orderId'] for row in list_orders({'fulfillment': task, **params})['items']}

    def counts(self, member=None):
        from fulfillment.task_reads import order_task_counts
        return order_task_counts(Order.objects.filter(member=member or self.member))

    def finish_refund(self, line, quantity):
        case = apply_case(self.member, line.id, 'REFUND_ONLY', quantity, '合成只读回归退款', uuid4())
        review_case(case.id, self.owner, True, '合成审核允许退款', case.revision)
        intent = prepare_refund(case.id, self.owner, uuid4())
        self.assertEqual(record_verified_refund(self.refund_evidence(intent))['outcome'], 'SUCCEEDED')

    def mixed(self):
        with transaction.atomic():
            product = Product.objects.create(product_no='TASK-SHIP', name='合成混合单实物',
                category=self.sku.product.category, fulfillment_kind='SHIP', status='ON_SALE',
                ever_on_sale=True, main_image=self.sku.product.main_image)
            sku = Sku.objects.create(product=product, sku_code='TASK-SHIP-SKU', spec_key='only',
                                     list_price_fen=1000, sale_status='ON_SALE')
            unit = SkuUnitVersion.objects.create(sku=sku, base_unit='件', sale_unit='件', ratio=1)
            sku.current_unit = unit
            sku.save(update_fields=['current_unit'])
            InventoryBalance.objects.create(warehouse=self.balance.warehouse, sku=sku, on_hand_base_units=5)
        address = CustomerAddress.objects.create(member=self.member, recipient_name='合成收件人',
            phone='13800000000', province='浙江', city='杭州', district='西湖', detail='合成地址')
        quote = create_quote({'items': [{'skuId': str(self.sku.id), 'quantity': 2},
                                       {'skuId': str(sku.id), 'quantity': 2}],
                              'addressId': str(address.id)}, self.member)
        data, _ = submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        order = Order.objects.get(pk=data['orderId'])
        record_verified_payment(self._evidence(order, trade_no=uuid4().hex, event_id=uuid4().hex))
        order.refresh_from_db()
        return order, order.lines.get(sku=sku), order.lines.get(sku=self.sku)

    def test_filter_runs_before_pagination_and_keeps_old_shape_and_intersections(self):
        shipping, _ = self._paid_order('SHIP')
        self._paid_order()
        self._paid_order()
        result = list_orders({'fulfillment': 'WAITING_SHIPMENT', 'pageSize': '1'})
        self.assertEqual((result['total'], result['page'], result['pageSize']), (1, 1, 1))
        self.assertEqual(result['items'][0]['orderId'], str(shipping.id))
        self.assertEqual(list_orders({'fulfillment': 'WAITING_SHIPMENT', 'page': '2', 'pageSize': '1'})['items'], [])
        self.assertEqual(self.ids('WAITING_SHIPMENT', status='PENDING_PAYMENT'), set())
        self.assertEqual(self.ids('WAITING_SHIPMENT', paymentMethod='WECHAT'), set())
        self.assertEqual(self.ids('WAITING_SHIPMENT', search=shipping.order_no), {str(shipping.id)})
        self.assertEqual(list_orders({}), list_orders({'fulfillment': ''}))

    def test_mixed_tasks_overlap_and_redemption_hold_does_not_hide_shipping(self):
        order, _, service = self.mixed()
        self.assertEqual(self.ids('WAITING_SHIPMENT'), {str(order.id)})
        self.assertEqual(self.ids('WAITING_REDEMPTION'), {str(order.id)})
        apply_case(self.member, service.id, 'REFUND_ONLY', 1, '合成部分冻结核销', uuid4())
        self.assertEqual(self.ids('AFTER_SALE'), {str(order.id)})
        self.assertEqual(self.ids('WAITING_SHIPMENT'), {str(order.id)})
        self.assertEqual(self.ids('WAITING_REDEMPTION'), {str(order.id)})
        counts = self.counts()
        self.assertEqual((counts['waitingShipment'], counts['waitingRedemption'], counts['afterSale']), (1, 1, 1))

    def test_shipping_holds_refunds_transit_and_receipt_follow_existing_facts(self):
        order, line = self._paid_order('SHIP')
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '合成冻结发货', uuid4())
        self.assertEqual(self.ids('WAITING_SHIPMENT'), set())
        self.assertEqual(self.ids('AFTER_SALE'), {str(order.id)})
        withdraw_case(case.id, self.member, case.revision)
        self.finish_refund(line, 1)
        self.assertEqual(self.ids('WAITING_SHIPMENT'), {str(order.id)})
        Carrier.objects.create(code='TASK', name='合成承运商', enabled=True)
        order.refresh_from_db()
        ship_order(order.id, self.owner, 'TASK', 'TASK123', order.revision, uuid4())
        self.assertEqual(self.ids('WAITING_SHIPMENT'), set())
        self.assertEqual(self.ids('IN_TRANSIT'), {str(order.id)})
        confirm_receipt(self.member, order.id)
        self.assertEqual(self.ids('IN_TRANSIT'), set())

    def test_shipping_hold_does_not_hide_redeemable_part_of_mixed_order(self):
        order, physical, _ = self.mixed()
        apply_case(self.member, physical.id, 'REFUND_ONLY', 1, '合成只冻结实物', uuid4())
        self.assertEqual(self.ids('WAITING_SHIPMENT'), set())
        self.assertEqual(self.ids('WAITING_REDEMPTION'), {str(order.id)})
        self.assertEqual(self.ids('AFTER_SALE'), {str(order.id)})

    def test_in_transit_and_active_return_are_overlapping_tasks(self):
        order, line = self._paid_order('SHIP')
        Carrier.objects.create(code='RETURN', name='合成退货承运商', enabled=True)
        ship_order(order.id, self.owner, 'RETURN', 'RETURN123', order.revision, uuid4())
        apply_case(self.member, line.id, 'RETURN_REFUND', 1, '合成运输中退货', uuid4())
        self.assertEqual(self.ids('IN_TRANSIT'), {str(order.id)})
        self.assertEqual(self.ids('AFTER_SALE'), {str(order.id)})

    def test_all_shipping_units_refunded_are_not_a_shipping_task(self):
        order, line = self._paid_order('SHIP')
        self.finish_refund(line, 2)
        self.assertEqual(self.ids('WAITING_SHIPMENT'), set())
        self.assertEqual(self.ids('AFTER_SALE'), set())
        self.assertEqual(self.counts()['waitingShipment'], 0)

    def test_part_redeemed_frozen_remaining_and_rejected_case(self):
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid4())
        self.assertEqual(self.ids('WAITING_REDEMPTION'), {str(order.id)})
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '合成冻结剩余核销', uuid4())
        self.assertEqual(self.ids('WAITING_REDEMPTION'), set())
        review_case(case.id, self.owner, False, '合成驳回释放冻结', case.revision)
        self.assertEqual(self.ids('WAITING_REDEMPTION'), {str(order.id)})
        self.finish_refund(line, 1)
        self.assertEqual(self.ids('WAITING_REDEMPTION'), set())

    def test_voucher_valid_on_last_day_but_not_after_expiry(self):
        last_day = timezone.localdate()
        order, _ = self._paid_order(valid_until=last_day)
        self.assertEqual(self.ids('WAITING_REDEMPTION'), {str(order.id)})
        with patch('fulfillment.task_reads.timezone.localdate', return_value=last_day + timedelta(days=1)):
            self.assertEqual(self.ids('WAITING_REDEMPTION'), set())
            self.assertEqual(self.counts()['waitingRedemption'], 0)

    def test_historical_missing_voucher_or_validity_is_not_redeemable(self):
        # Same historical missing-voucher fixture used by fulfillment tests.
        with patch('fulfillment.service.issue_paid_vouchers_locked'):
            _, line = self._paid_order()
        self.assertEqual(self.ids('WAITING_REDEMPTION'), set())
        from fulfillment.codes import new_nonce, voucher_code, code_digest
        nonce = new_nonce()
        RedeemVoucher.objects.create(order_line=line, nonce=nonce,
            code_digest=code_digest(voucher_code(line.id, nonce)), valid_until=None)
        self.assertEqual(self.ids('WAITING_REDEMPTION'), set())
        self.assertEqual(self.counts()['waitingRedemption'], 0)

    def test_points_orders_share_task_counts_and_order_kind_filter(self):
        grant_points(self.member, 1000, timezone.now() + timedelta(days=10), 'synthetic-task-points')
        points, _, _ = exchange_fixtures.D5ExchangeTests.exchange(self, 2, 'REDEEM')
        cash, _ = self._paid_order()
        self.assertEqual(self.ids('WAITING_REDEMPTION'), {str(points.id), str(cash.id)})
        self.assertEqual(self.ids('WAITING_REDEMPTION', orderKind='POINTS'), {str(points.id)})
        self.assertEqual(self.counts()['waitingRedemption'], 2)

    def test_pending_counts_use_persisted_status_without_closing_orders(self):
        order = self._order()
        with patch('fulfillment.task_reads.timezone.localdate', return_value=timezone.localdate() + timedelta(days=2)):
            self.assertEqual(self.counts()['pendingPayment'], 1)
        self.assertEqual(self.ids('WAITING_REDEMPTION'), set())
        cancel_order(self.member, order.id)
        self.assertEqual(self.counts()['pendingPayment'], 0)

    def test_member_overview_and_http_filter_are_private_and_consistent(self):
        order, _ = self._paid_order('SHIP')
        token, _ = MemberSession.issue(self.member)
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
        client = Client()
        overview = client.get('/api/v1/app/member/overview', **headers)
        self.assertEqual(overview.status_code, 200)
        data = overview.json()['data']
        self.assertEqual(data['orderCounts'], self.counts())
        self.assertTrue({'grade', 'points', 'rules', 'effectiveSpendFen'} <= data.keys())
        self.assertIn('no-store', overview['Cache-Control'])
        self.assertIn('Authorization', overview['Vary'])
        result = client.get('/api/v1/app/orders', {'fulfillment': 'WAITING_SHIPMENT'}, **headers)
        self.assertEqual(result.json()['data']['items'][0]['orderId'], str(order.id))
        self.assertEqual(result.json()['meta'], {'page': 1, 'pageSize': 20, 'total': 1})
        other = Member.objects.create(wechat_app_id='wx-payment-test', wechat_openid='task-other', grade=self.member.grade)
        other_token, _ = MemberSession.issue(other)
        other_headers = {'HTTP_AUTHORIZATION': 'Bearer ' + other_token}
        self.assertEqual(client.get('/api/v1/app/orders', {'fulfillment': 'WAITING_SHIPMENT'}, **other_headers).json()['data']['total'], 0)
        self.assertEqual(client.get('/api/v1/app/member/overview', **other_headers).json()['data']['orderCounts'],
                         dict.fromkeys(data['orderCounts'], 0))
        self.assertEqual(client.get('/api/v1/app/member/overview').status_code, 401)
        self.assertEqual(client.get('/api/v1/app/orders').status_code, 401)
        self.assertEqual(client.get('/api/v1/app/orders', {'fulfillment': 'INVALID'}, **headers).status_code, 400)

    def test_admin_filter_keeps_auth_permission_and_rejects_duplicate_parameter(self):
        client = Client()
        path = '/api/v1/admin/orders'
        self.assertEqual(client.get(path).status_code, 401)
        for actor, expected in [(self.owner, 200), (self.checker, 403)]:
            client.get('/api/v1/admin/auth/csrf')
            password = 'Long test password 2026!' if actor == self.owner else 'Synthetic checker password 2026!'
            login = client.post('/api/v1/admin/auth/login', json.dumps({'loginName': actor.login_name, 'password': password}), content_type='application/json')
            self.assertEqual(login.status_code, 200)
            self.assertEqual(client.get(path, {'fulfillment': 'WAITING_SHIPMENT'}).status_code, expected)
            if expected == 200:
                self.assertEqual(client.get(path + '?fulfillment=IN_TRANSIT&fulfillment=AFTER_SALE').status_code, 400)
            client.post('/api/v1/admin/auth/logout', '{}', content_type='application/json')

    def test_query_count_is_bounded_and_counts_use_one_aggregate(self):
        self._paid_order()
        with self.assertNumQueries(1):
            self.counts()
        with CaptureQueriesContext(connection) as small:
            list_orders({'fulfillment': 'WAITING_REDEMPTION'})
        self._paid_order()
        self._paid_order()
        with CaptureQueriesContext(connection) as larger:
            list_orders({'fulfillment': 'WAITING_REDEMPTION'})
        self.assertEqual(len(small), len(larger))
        self.assertLessEqual(len(larger), 7)
