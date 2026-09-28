"""A completed partial refund leaves unrefunded physical units deliverable."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
from django.test import TransactionTestCase,RequestFactory,override_settings
from django.utils import timezone
from fulfillment.models import Carrier,Shipment
from fulfillment.service import ship_order,FulfillmentError,confirm_receipt
from fulfillment.read import page_fulfillment_summaries,fulfillment_data
from payments.tests import test_refund_flow as refund_flow
from points_exchange.tests import test_d5_exchange as exchange_flow
from aftersales.service import apply_case,review_case
from benefits.service import grant_points
from benefits.models import PointsAccount
from inventory.models import InventoryLedger


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',EXCHANGE_ORDER_ENABLED=True,
                   ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE':True,'WECHAT':True})
class RemainingShippingTests(TransactionTestCase):
    setUp_fixture=refund_flow.RefundFlowTests.setUp
    _paid_order=refund_flow.RefundFlowTests._paid_order
    _order=refund_flow.RefundFlowTests._order
    _evidence=refund_flow.RefundFlowTests._evidence
    refund_evidence=refund_flow.RefundFlowTests.refund_evidence

    def setUp(self):
        self.setUp_fixture()
        grant_points(self.member,1000,timezone.now()+timedelta(days=10),'remaining-shipping-synthetic')
        Carrier.objects.create(code='REMAIN',name='合成剩余发货',enabled=True)

    def paid(self,kind):
        if kind=='CASH':return self._paid_order('SHIP')
        order,line,_=exchange_flow.D5ExchangeTests.exchange(self,2,'SHIP',price=100)
        return order,line

    def complete(self,case,kind):
        case.refresh_from_db()
        if kind=='CASH':
            from payments.refunds import prepare_refund,record_verified_refund
            intent=prepare_refund(case.id,self.owner,uuid4())
            self.assertEqual(record_verified_refund(self.refund_evidence(intent))['outcome'],'SUCCEEDED')
        else:
            from payments.benefit_settlements import settle_benefits
            request=RequestFactory().post('/');request.request_id=uuid4()
            with patch('payments.benefit_settlements.confirm_action',return_value=None):
                _,_,bad=settle_benefits(request,self.owner,case.id,{'expectedRevision':case.revision},uuid4())
            self.assertIsNone(bad)

    def test_cash_and_points_completed_partial_refund_can_ship_remaining_without_stock_deduction(self):
        for kind in ['CASH','POINTS']:
            with self.subTest(kind=kind):
                order,line=self.paid(kind)
                case=apply_case(self.member,line.id,'REFUND_ONLY',1,'合成部分取消',uuid4())
                review_case(case.id,self.owner,True,'合成退款审核',case.revision);self.complete(case,kind)
                order.refresh_from_db();self.balance.refresh_from_db();before=self.balance.on_hand_base_units
                points_before=PointsAccount.objects.get(member=self.member).settled_points
                self.assertTrue(page_fulfillment_summaries([order])[order.id]['shipEligible'])
                key=uuid4();first=ship_order(order.id,self.owner,'REMAIN','REMAIN-'+kind,order.revision,key)
                replay=ship_order(order.id,self.owner,'REMAIN','REMAIN-'+kind,order.revision,key)
                self.assertEqual(first['shipment'],replay['shipment'])
                self.assertEqual(first['items'][0]['fulfillment']['refundedQuantity'],1)
                self.assertFalse(page_fulfillment_summaries([order])[order.id]['shipEligible'])
                self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,before)
                self.assertEqual(InventoryLedger.objects.filter(order_line=line,movement_type='SALE').count(),1)
                confirm_receipt(self.member,order.id)
                self.assertEqual(fulfillment_data(order)['fulfillmentStatus'],'COMPLETED')
                if kind=='POINTS':
                    from customers.models import ConsumptionEvent,ConsumptionOrder
                    self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,points_before)
                    self.assertFalse(ConsumptionEvent.objects.filter(order_id=order.id).exists())
                    self.assertFalse(ConsumptionOrder.objects.filter(order_id=order.id).exists())

    def test_cash_and_points_active_cases_block_shipping_until_partial_refund_completes(self):
        for kind in ['CASH','POINTS']:
            with self.subTest(kind=kind):
                order,line=self.paid(kind)
                case=apply_case(self.member,line.id,'REFUND_ONLY',1,'合成尚未核实退款',uuid4())
                for stage in ['REQUESTED','APPROVED']:
                    order.refresh_from_db()
                    self.assertFalse(page_fulfillment_summaries([order])[order.id]['shipEligible'])
                    with self.assertRaises(FulfillmentError) as caught:
                        ship_order(order.id,self.owner,'REMAIN','HOLD-'+kind,order.revision,uuid4())
                    self.assertEqual(caught.exception.code,'AFTERSALE_BLOCKS_SHIPMENT')
                    if stage=='REQUESTED':review_case(case.id,self.owner,True,'合成审核后仍占用',case.revision)
                self.assertFalse(Shipment.objects.filter(order=order).exists())
                self.complete(case,kind);order.refresh_from_db()
                ship_order(order.id,self.owner,'REMAIN','READY-'+kind,order.revision,uuid4())
                self.assertTrue(Shipment.objects.filter(order=order).exists())

    def test_cash_and_points_fully_refunded_shipping_lines_cannot_create_shipment(self):
        for kind in ['CASH','POINTS']:
            with self.subTest(kind=kind):
                order,line=self.paid(kind)
                case=apply_case(self.member,line.id,'REFUND_ONLY',2,'合成全部取消',uuid4())
                review_case(case.id,self.owner,True,'合成全部退款审核',case.revision);self.complete(case,kind)
                order.refresh_from_db()
                self.assertFalse(page_fulfillment_summaries([order])[order.id]['shipEligible'])
                with self.assertRaises(FulfillmentError):
                    ship_order(order.id,self.owner,'REMAIN','FULL-'+kind,order.revision,uuid4())
                self.assertFalse(Shipment.objects.filter(order=order).exists())

    def test_fully_refunded_shipping_item_is_excluded_when_another_item_remains(self):
        from django.db import transaction
        from catalog.models import Product,Sku,SkuUnitVersion
        from customers.models import CustomerAddress
        from inventory.models import InventoryBalance
        from orders.models import Order
        from orders.service import submit_order
        from checkout.service import create_quote
        from payments.service import record_verified_payment
        from aftersales.guards import assert_shippable_locked
        product=self.sku.product;product.fulfillment_kind='SHIP';product.save(update_fields=['fulfillment_kind'])
        with transaction.atomic():
            physical=Product.objects.create(product_no='REMAIN-SECOND',name='合成另一发货项',category=product.category,
                fulfillment_kind='SHIP',status='ON_SALE',ever_on_sale=True,main_image=product.main_image)
            sku=Sku.objects.create(product=physical,sku_code='REMAIN-SECOND-SKU',spec_key='only',list_price_fen=1000,sale_status='ON_SALE')
            unit=SkuUnitVersion.objects.create(sku=sku,base_unit='件',sale_unit='件',ratio=1)
            sku.current_unit=unit;sku.save(update_fields=['current_unit'])
            InventoryBalance.objects.create(warehouse=self.balance.warehouse,sku=sku,on_hand_base_units=5)
        address=CustomerAddress.objects.create(member=self.member,recipient_name='合成混合收件',phone='13800000000',province='浙江',city='杭州',district='西湖',detail='合成地址')
        quote=create_quote({'items':[{'skuId':str(self.sku.id),'quantity':1},{'skuId':str(sku.id),'quantity':1}],
                            'addressId':str(address.id)},self.member)
        created,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid4())
        order=Order.objects.get(pk=created['orderId'])
        record_verified_payment(self._evidence(order,trade_no=uuid4().hex,event_id=uuid4().hex))
        first=order.lines.get(sku=self.sku);second=order.lines.get(sku=sku)
        case=apply_case(self.member,first.id,'REFUND_ONLY',1,'合成整项取消',uuid4())
        review_case(case.id,self.owner,True,'合成整项退款审核',case.revision);self.complete(case,'CASH')
        order.refresh_from_db()
        with transaction.atomic():
            Order.objects.select_for_update().get(pk=order.id)
            self.assertEqual([line.id for line in assert_shippable_locked(order,[first,second])],[second.id])
        self.assertTrue(page_fulfillment_summaries([order])[order.id]['shipEligible'])
        data=ship_order(order.id,self.owner,'REMAIN','REMAIN-MIXED',order.revision,uuid4())
        by_id={row['orderLineId']:row for row in data['items']}
        self.assertEqual(by_id[str(first.id)]['fulfillment']['status'],'REFUNDED')
        self.assertEqual(by_id[str(second.id)]['fulfillment']['status'],'IN_TRANSIT')
        self.assertEqual(confirm_receipt(self.member,order.id)['fulfillmentStatus'],'COMPLETED')
