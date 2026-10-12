"""List operations derive each capability from the same line's persisted facts."""
from datetime import timedelta
from uuid import uuid4
from django.db import transaction
from django.http import QueryDict
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from unittest.mock import patch
from catalog.models import Product, Sku, SkuUnitVersion
from inventory.models import InventoryBalance
from shipping.tests.helpers import selected_physical_quote as create_quote
from orders.models import Order
from orders.service import submit_order
from orders.queries import list_orders
from payments.service import record_verified_payment, PaymentError
from fulfillment.models import Carrier, RedeemVoucher
from fulfillment.service import ship_order, redeem
from fulfillment.read import page_fulfillment_summaries
from aftersales.service import apply_case, withdraw_case
from . import test_flow as fixtures


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE': True, 'WECHAT': True})
class AdminListCapabilitiesTests(TransactionTestCase):
    setUp = fixtures.FulfillmentFlowTests.setUp
    _order = fixtures.FulfillmentFlowTests._order
    _evidence = fixtures.FulfillmentFlowTests._evidence
    _paid_order = fixtures.FulfillmentFlowTests._paid_order

    def test_summary_has_shipment_and_remaining_redeemable_quantity(self):
        order, line = self._paid_order()
        summary = page_fulfillment_summaries([order])[order.id]
        self.assertTrue(summary['redeemEligible'])
        self.assertFalse(summary['hasShipment'])
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '冻结部分核销数量', uuid4())
        self.assertTrue(page_fulfillment_summaries([order])[order.id]['redeemEligible'])
        withdraw_case(case.id, self.member, case.revision)
        apply_case(self.member, line.id, 'REFUND_ONLY', 2, '冻结全部核销数量', uuid4())
        self.assertFalse(page_fulfillment_summaries([order])[order.id]['redeemEligible'])
        shipping, _ = self._paid_order('SHIP')
        self.assertFalse(page_fulfillment_summaries([shipping])[shipping.id]['hasShipment'])
        Carrier.objects.create(code='LIST', name='验收快递', enabled=True)
        ship_order(shipping.id, self.owner, 'LIST', 'LIST123456', shipping.revision, uuid4())
        self.assertTrue(page_fulfillment_summaries([shipping])[shipping.id]['hasShipment'])
        self.assertFalse(page_fulfillment_summaries([shipping])[shipping.id]['redeemEligible'])

    def test_validity_and_quantity_must_belong_to_the_same_redeem_line(self):
        with transaction.atomic():
            product = Product.objects.create(product_no='LIST-VALID', name='较长有效期核销商品', category=self.sku.product.category,
                fulfillment_kind='REDEEM', status='ON_SALE', ever_on_sale=True,
                main_image=self.sku.product.main_image, redeem_valid_until=timezone.localdate()+timedelta(days=90))
            sku = Sku.objects.create(product=product, sku_code='LIST-VALID', spec_key='only', list_price_fen=1000, sale_status='ON_SALE')
            unit = SkuUnitVersion.objects.create(sku=sku, base_unit='件', sale_unit='件', ratio=1)
            sku.current_unit = unit
            sku.save(update_fields=['current_unit'])
            InventoryBalance.objects.create(warehouse=self.balance.warehouse, sku=sku, on_hand_base_units=10)
        quote = create_quote({'items': [{'skuId': str(self.sku.id), 'quantity': 2}, {'skuId': str(sku.id), 'quantity': 2}]}, self.member)
        data, _ = submit_order(self.member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid4())
        order = Order.objects.get(pk=data['orderId'])
        record_verified_payment(self._evidence(order, trade_no=uuid4().hex, event_id=uuid4().hex))
        order.refresh_from_db()
        valid = RedeemVoucher.objects.get(order_line__order=order, order_line__sku=sku)
        redeem(valid.id, self.owner, 2, valid.revision, uuid4())
        with patch('fulfillment.read.timezone.localdate', return_value=timezone.localdate()+timedelta(days=40)):
            self.assertFalse(page_fulfillment_summaries([order])[order.id]['redeemEligible'])

    def test_summary_query_count_does_not_grow_and_unpaid_is_never_redeemable(self):
        first, _ = self._paid_order()
        second = self._order()
        with self.assertNumQueries(5):
            page_fulfillment_summaries([first])
        with self.assertNumQueries(5):
            summary = page_fulfillment_summaries([first, second])
        self.assertFalse(summary[second.id]['redeemEligible'])

    def test_order_ids_filters_exact_scope_before_pagination_and_rejects_invalid_values(self):
        first = self._order()
        second = self._order()
        third = self._order()
        result = list_orders({'orderIds': f'{first.id},{third.id}', 'pageSize': '1'})
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['items']), 1)
        self.assertNotEqual(result['items'][0]['orderId'], str(second.id))
        self.assertEqual(list_orders({'orderIds': str(uuid4())})['total'], 0)
        self.assertEqual(list_orders({})['total'], 3)
        for value in ['', 'invalid', str(first.id)+',', f'{first.id},{first.id}', ','.join(str(uuid4()) for _ in range(101))]:
            with self.subTest(value=value[:40]), self.assertRaises(PaymentError):
                list_orders({'orderIds': value})
        repeated = QueryDict('orderIds='+str(first.id)+'&orderIds='+str(second.id))
        with self.assertRaises(PaymentError):
            list_orders(repeated)
