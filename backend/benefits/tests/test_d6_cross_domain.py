"""D6 exit evidence through real order, fulfillment, refund and benefit entrypoints."""
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone

from payments.tests import test_refund_flow as refund_fixture
from benefits.models import BenefitLedger, CouponCampaign, MemberCoupon, PointsAccount, PointsGrant
from benefits.service import grant_points
from customers.models import CustomerAddress, ConsumptionOrder
from inventory.models import InventoryBalance, InventoryLedger
from orders.models import Order
from payments.models import PaymentReceipt, RefundEvidence, RefundIntent
from points_exchange.models import ExchangeOffer


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', EXCHANGE_ORDER_ENABLED=True,
                   ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE': True, 'WECHAT': True})
class D6CrossDomainTests(TransactionTestCase):
    setUp_fixture = refund_fixture.RefundFlowTests.setUp
    _paid_order = refund_fixture.RefundFlowTests._paid_order
    _order = refund_fixture.RefundFlowTests._order
    _evidence = refund_fixture.RefundFlowTests._evidence
    refund_evidence = refund_fixture.RefundFlowTests.refund_evidence

    def setUp(self):
        self.setUp_fixture()

    def _refund_money(self, case, *, fault_once=False):
        from payments.refunds import prepare_refund, record_verified_refund
        case.refresh_from_db()
        intent = prepare_refund(case.id, self.owner, uuid4())
        evidence = self.refund_evidence(intent)
        if fault_once:
            with patch('payments.refunds._finalize_case', side_effect=RuntimeError('D6 synthetic finalization fault')):
                self.assertEqual(record_verified_refund(evidence)['outcome'], 'SETTLEMENT_FAILED')
            case.refresh_from_db();self.assertEqual(case.status, 'WAITING_REFUND')
            from payments.refunds import recover_recorded_refunds
            self.assertEqual(recover_recorded_refunds()['succeeded'], 1)
        self.assertEqual(record_verified_refund(evidence)['outcome'], 'SUCCEEDED')
        self.assertEqual(record_verified_refund(evidence)['outcome'], 'SUCCEEDED')
        case.refresh_from_db()
        self.assertEqual(case.status, 'COMPLETED')
        return intent

    def _refund_points(self, case):
        from payments.benefit_settlements import settle_benefits
        case.refresh_from_db()
        request = RequestFactory().post('/'); request.request_id = uuid4()
        with patch('payments.benefit_settlements.confirm_action', return_value=None):
            finished, replay, denied = settle_benefits(request, self.owner, case.id,
                {'expectedRevision': case.revision}, uuid4())
        self.assertIsNone(denied)
        self.assertFalse(replay)
        self.assertEqual(finished.status, 'COMPLETED')

    def test_completed_cash_reward_spent_on_exchange_then_partial_refund_creates_debt(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem
        from points_exchange.orders import create_exchange_quote, submit_exchange_order
        from aftersales.service import apply_case, review_case
        cash, line = self._paid_order('REDEEM')
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 2, voucher.revision, uuid4())
        reward = PointsAccount.objects.get(member=self.member).settled_points
        self.assertEqual(reward, cash.payable_fen // 100)
        self.assertEqual(ConsumptionOrder.objects.get(order_id=cash.id).effective_spend_fen, cash.payable_fen)
        offer = ExchangeOffer.objects.create(sku=self.sku, points_price=reward, status='ON_SALE')
        quote = create_exchange_quote(self.member, {'offerId':str(offer.id),'quantity':1})
        exchange, replay = submit_exchange_order(self.member, {'quoteId':quote['quoteId']}, uuid4())
        self.assertFalse(replay)
        self.assertEqual(exchange['exchangePoints'], reward)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, 'D6已核销部分退款', uuid4(), redemption_scope='USED')
        review_case(case.id, self.owner, True, 'D6审核已核销退款', case.revision)
        intent = self._refund_money(case)
        debt = PointsAccount.objects.get(member=self.member).settled_points
        self.assertLess(debt, 0)
        self.assertEqual(debt, -(cash.payable_fen // 2 // 100))
        self.assertEqual(ConsumptionOrder.objects.get(order_id=cash.id).effective_spend_fen, cash.payable_fen - intent.amount_fen)
        self.assertFalse(ConsumptionOrder.objects.filter(order_id=exchange['orderId']).exists())
        self.assertEqual(BenefitLedger.objects.filter(order_id=cash.id,kind='CLAWBACK').count(), 1)
        self.assertTrue(BenefitLedger.objects.filter(order_id=cash.id,kind='CLAWBACK',cause_ref='case:'+str(case.id)).exists())
        topup = grant_points(self.member, -debt+5, timezone.now()+timedelta(days=30), 'd6-debt-recovery')
        self.assertEqual(topup.available_points, 5)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 5)
        self.assertEqual(RefundEvidence.objects.filter(intent=intent).count(), 1)

    def test_cash_coupon_and_points_partial_unshipped_refund_then_return_acceptance(self):
        from checkout.service import create_quote
        from orders.service import submit_order
        from payments.service import record_verified_payment
        from aftersales.service import apply_case, review_case
        from aftersales.returns import accept_return
        from fulfillment.models import Carrier
        from fulfillment.service import ship_order, confirm_receipt
        from benefits.lifecycle import order_benefit_data
        product=self.sku.product;product.fulfillment_kind='SHIP';product.save(update_fields=['fulfillment_kind'])
        address=CustomerAddress.objects.create(member=self.member,recipient_name='D6合成收件',phone='13800000000',
            province='浙江',city='杭州',district='西湖',detail='D6合成路1号')
        now=timezone.now()
        campaign=CouponCampaign.objects.create(code='D6-CASH-COUPON',title='D6合成券',kind='CASH',discount_fen=200,
            valid_from=now-timedelta(days=1),valid_until=now+timedelta(days=30))
        coupon=MemberCoupon.objects.create(member=self.member,campaign=campaign)
        grant_points(self.member,1000,now+timedelta(days=30),'d6-deduct-lot')
        quote=create_quote({'items':[{'skuId':str(self.sku.id),'quantity':2}],
                            'addressId':str(address.id),'couponId':str(coupon.id),'pointsToUse':200},self.member)
        self.assertTrue(quote['ready'])
        created,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid4())
        order=Order.objects.get(pk=created['orderId']);line=order.lines.get()
        record_verified_payment(self._evidence(order,trade_no=uuid4().hex,event_id=uuid4().hex))
        order.refresh_from_db();coupon.refresh_from_db()
        self.assertEqual(coupon.status,'USED')
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,800)
        first=apply_case(self.member,line.id,'REFUND_ONLY',1,'D6未发货部分退款',uuid4())
        review_case(first.id,self.owner,True,'D6审核部分退款',first.revision)
        self._refund_money(first, fault_once=True)
        self.assertEqual(order_benefit_data(order.id)['returnedPoints'],100)
        Carrier.objects.create(code='D6',name='D6合成物流',enabled=True)
        order.refresh_from_db();ship_order(order.id,self.owner,'D6','D6SHIP123',order.revision,uuid4())
        self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,9)
        confirm_receipt(self.member,order.id)
        second=apply_case(self.member,line.id,'RETURN_REFUND',1,'D6剩余商品退货',uuid4())
        review_case(second.id,self.owner,True,'D6同意剩余退货',second.revision)
        second.refresh_from_db()
        body={'expectedRevision':second.revision,'mode':'RECEIVED','receivedQuantity':1,'salableQuantity':1,
              'refundQuantity':1,'refundAmountFen':None,'reason':'D6合成剩余实物验收'}
        acceptance,replay=accept_return(second.id,self.owner,body,uuid4())
        self.assertFalse(replay);self.assertEqual(acceptance.salable_quantity,1)
        self._refund_money(second)
        from aftersales.refund_amounts import amount_components
        self.assertEqual(amount_components(second)['shippingRefundAmountFen'], 0)
        self.balance.refresh_from_db();coupon.refresh_from_db()
        self.assertEqual(self.balance.on_hand_base_units,10)
        self.assertEqual(order_benefit_data(order.id)['returnedPoints'],200)
        self.assertEqual(coupon.status,'USED')
        self.assertEqual(InventoryLedger.objects.filter(order_line=line,movement_type='SALE').count(),1)
        self.assertEqual(InventoryLedger.objects.filter(refund_order_line=line,movement_type='REFUND').count(),1)
        self.assertEqual(InventoryLedger.objects.filter(return_order_line=line,movement_type='RETURN').count(),1)
        self.assertEqual(RefundEvidence.objects.filter(intent__case__order_line=line).count(),2)

    def test_points_ship_and_redeem_refunds_share_lots_without_cash_receipts(self):
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        from fulfillment.models import Carrier,RedeemVoucher
        from fulfillment.service import ship_order,confirm_receipt,redeem
        from aftersales.service import apply_case,review_case
        from benefits.lifecycle import order_benefit_data
        from catalog.models import Product,Sku,SkuUnitVersion
        grant_points(self.member,1000,timezone.now()+timedelta(days=30),'d6-exchange-lot')
        product=self.sku.product;product.fulfillment_kind='SHIP';product.save(update_fields=['fulfillment_kind'])
        address=CustomerAddress.objects.create(member=self.member,recipient_name='D6积分收件',phone='13800000000',
            province='浙江',city='杭州',district='西湖',detail='D6积分路1号')
        ship_offer=ExchangeOffer.objects.create(sku=self.sku,points_price=100,status='ON_SALE')
        ship_quote=create_exchange_quote(self.member,{'offerId':str(ship_offer.id),'quantity':2,'addressId':str(address.id)})
        ship_data,_=submit_exchange_order(self.member,{'quoteId':ship_quote['quoteId']},uuid4())
        ship_order_row=Order.objects.get(pk=ship_data['orderId']);ship_line=ship_order_row.lines.get()
        first=apply_case(self.member,ship_line.id,'REFUND_ONLY',1,'D6积分未发货取消',uuid4())
        review_case(first.id,self.owner,True,'D6批准积分取消',first.revision);self._refund_points(first)
        Carrier.objects.create(code='D6P',name='D6积分物流',enabled=True)
        ship_order_row.refresh_from_db();ship_order(ship_order_row.id,self.owner,'D6P','D6POINT123',ship_order_row.revision,uuid4())
        confirm_receipt(self.member,ship_order_row.id)
        with transaction.atomic():
            redeem_product=Product.objects.create(product_no='D6-REDEEM',name='D6积分核销品',category=product.category,
                fulfillment_kind='REDEEM',status='ON_SALE',ever_on_sale=True,main_image=product.main_image,
                redeem_valid_until=timezone.localdate()+timedelta(days=30))
            redeem_sku=Sku.objects.create(product=redeem_product,sku_code='D6-REDEEM-SKU',spec_key='only',list_price_fen=1000,sale_status='ON_SALE')
            unit=SkuUnitVersion.objects.create(sku=redeem_sku,base_unit='件',sale_unit='件',ratio=1)
            redeem_sku.current_unit=unit;redeem_sku.save(update_fields=['current_unit'])
            InventoryBalance.objects.create(warehouse=self.balance.warehouse,sku=redeem_sku,on_hand_base_units=10)
        redeem_offer=ExchangeOffer.objects.create(sku=redeem_sku,points_price=150,status='ON_SALE')
        quote=create_exchange_quote(self.member,{'offerId':str(redeem_offer.id),'quantity':2})
        redeem_data,_=submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        redeem_order=Order.objects.get(pk=redeem_data['orderId']);line=redeem_order.lines.get()
        voucher=RedeemVoucher.objects.get(order_line=line)
        redeemed=redeem(voucher.id,self.owner,1,voucher.revision,uuid4())
        self.assertFalse(redeemed['replayed'])
        unused=apply_case(self.member,line.id,'REFUND_ONLY',1,'D6积分未核销部分退款',uuid4())
        review_case(unused.id,self.owner,True,'D6批准未核销退款',unused.revision);self._refund_points(unused)
        self.assertEqual(order_benefit_data(ship_order_row.id)['returnedPoints'],100)
        self.assertEqual(order_benefit_data(redeem_order.id)['returnedPoints'],150)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,750)
        from customers.models import ConsumptionEvent
        for pure_order in (ship_order_row, redeem_order):
            self.assertFalse(PaymentReceipt.objects.filter(order=pure_order).exists())
            self.assertFalse(RefundIntent.objects.filter(case__order_line__order=pure_order).exists())
            self.assertFalse(ConsumptionEvent.objects.filter(order_id=pure_order.id).exists())
            self.assertFalse(ConsumptionOrder.objects.filter(order_id=pure_order.id).exists())
        self.assertEqual(sum(PointsGrant.objects.filter(source_ref__startswith='return:').values_list('original_points',flat=True)),250)

    def test_after_sale_application_and_shipping_concurrency_has_one_consistent_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from fulfillment.models import Carrier, Shipment
        from fulfillment.service import FulfillmentError, ship_order
        from aftersales.models import AfterSaleCase
        from aftersales.service import AfterSaleError, apply_case
        order,line=self._paid_order('SHIP')
        Carrier.objects.create(code='D6-RACE',name='D6并发物流',enabled=True)
        barrier=Barrier(2)
        def run(kind):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout='5s'; SET statement_timeout='8s'")
                barrier.wait(timeout=5)
                try:
                    if kind=='SHIP':
                        ship_order(order.id,self.owner,'D6-RACE','D6RACE123',order.revision,uuid4())
                    else:
                        apply_case(self.member,line.id,'REFUND_ONLY',1,'D6并发申请取消',uuid4())
                    return kind,'SUCCEEDED'
                except (FulfillmentError,AfterSaleError) as exc:
                    return kind,exc.code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=dict(pool.map(run,['SHIP','CASE']))
        self.assertEqual(sum(value=='SUCCEEDED' for value in outcomes.values()),1,outcomes)
        has_shipment=Shipment.objects.filter(order=order).exists()
        has_case=AfterSaleCase.objects.filter(order_line=line).exists()
        self.assertNotEqual(has_shipment,has_case)
        self.assertEqual((outcomes['SHIP']=='SUCCEEDED',outcomes['CASE']=='SUCCEEDED'),(has_shipment,has_case))
        self.balance.refresh_from_db()
        self.assertEqual((self.balance.on_hand_base_units,self.balance.reserved_base_units),(8,0))
        self.assertFalse(RefundEvidence.objects.exists())
