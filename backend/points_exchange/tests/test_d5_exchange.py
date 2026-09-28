from datetime import timedelta
from uuid import uuid4
from django.test import TestCase,override_settings
from django.utils import timezone
from payments.tests import test_payment_flow as payment_flow
from benefits.service import grant_points
from benefits.models import PointsAccount
from orders.models import Order


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',EXCHANGE_ORDER_ENABLED=True)
class D5ExchangeTests(TestCase):
    def setUp(self):
        payment_flow.PaymentFlowTests.setUp(self)
        from accounts.models import AdminAccount
        self.owner=AdminAccount.objects.get(login_name='payment-owner')
        grant_points(self.member,1000,timezone.now()+timedelta(days=10),'synthetic-d5-start')

    def test_exchange_price_is_independent_and_stock_points_commit_once(self):
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=101,status='ON_SALE')
        quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':2})
        key=uuid4(); result,replay=submit_exchange_order(self.member,{'quoteId':quote['quoteId']},key)
        self.assertFalse(replay)
        replay_result,replay=submit_exchange_order(self.member,{'quoteId':quote['quoteId']},key)
        self.assertTrue(replay); self.assertEqual(result,replay_result)
        order=Order.objects.get(pk=result['orderId'])
        self.assertEqual((order.order_kind,order.status,order.points_to_use,order.payable_fen),('POINTS','PAID',202,0))
        self.balance.refresh_from_db(); self.assertEqual(self.balance.on_hand_base_units,8)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,798)

    def exchange(self,quantity=2,kind='REDEEM',price=101):
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        product=self.sku.product;product.fulfillment_kind=kind;product.save(update_fields=['fulfillment_kind'])
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=price,status='ON_SALE')
        body={'offerId':str(offer.id),'quantity':quantity}
        if kind=='SHIP':
            from customers.models import CustomerAddress
            address=CustomerAddress.objects.create(member=self.member,recipient_name='合成收件人',phone='13800000000',
                province='浙江',city='杭州',district='西湖',detail='合成地址')
            body['addressId']=str(address.id)
        quote=create_exchange_quote(self.member,body)
        data,_=submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        order=Order.objects.get(pk=data['orderId'])
        return order,order.lines.get(),offer

    def finish_case(self,case):
        from unittest.mock import patch
        from django.test import RequestFactory
        from aftersales.service import review_case
        from payments.benefit_settlements import settle_benefits
        review_case(case.id,self.owner,True,'合成批准退款',case.revision);case.refresh_from_db()
        request=RequestFactory().post('/');request.request_id=uuid4()
        with patch('payments.benefit_settlements.confirm_action',return_value=None):
            done,_,_=settle_benefits(request,self.owner,case.id,{'expectedRevision':case.revision},uuid4())
        return done

    def test_partial_and_final_refunds_return_exact_points_no_cash_evidence(self):
        from aftersales.service import apply_case
        from benefits.lifecycle import order_benefit_data
        from payments.models import PaymentReceipt,RefundIntent
        from fulfillment.models import RedeemVoucher
        order,line,_=self.exchange(3)
        first=apply_case(self.member,line.id,'REFUND_ONLY',1,'合成第一笔取消',uuid4())
        self.finish_case(first)
        self.assertEqual(order_benefit_data(order.id)['returnedPoints'],101)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,798)
        final=apply_case(self.member,line.id,'REFUND_ONLY',2,'合成第二笔取消',uuid4())
        self.finish_case(final)
        self.assertEqual(order_benefit_data(order.id)['returnedPoints'],303)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,1000)
        self.assertFalse(PaymentReceipt.objects.exists());self.assertFalse(RefundIntent.objects.exists())
        self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,7)
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity,3)

    def test_fulfillment_does_not_earn_or_activate_grade_rules(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem
        from customers.models import MemberConsumption,ConsumptionEvent
        from benefits.models import BenefitLedger
        order,line,_=self.exchange(2)
        voucher=RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id,self.owner,2,voucher.revision,uuid4())
        self.assertFalse(ConsumptionEvent.objects.filter(order_id=order.id).exists())
        self.assertFalse(MemberConsumption.objects.filter(member=self.member).exists())
        self.assertFalse(BenefitLedger.objects.filter(order_id=order.id,kind='EARN').exists())
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,798)

    def test_used_redemption_refund_returns_points_without_restoring_stock(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem
        from aftersales.service import apply_case
        order,line,_=self.exchange(2);voucher=RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id,self.owner,1,voucher.revision,uuid4())
        case=apply_case(self.member,line.id,'REFUND_ONLY',1,'合成已核销售后',uuid4(),redemption_scope='USED')
        self.finish_case(case)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,899)
        self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,8)

    def test_shipping_free_and_completed_delivery_no_second_stock_or_reward(self):
        from fulfillment.models import Carrier
        from fulfillment.service import ship_order,confirm_receipt
        from benefits.models import BenefitLedger
        order,line,_=self.exchange(2,'SHIP')
        self.assertEqual(order.shipping_fee_fen,0)
        Carrier.objects.create(code='D5',name='合成物流',enabled=True)
        ship_order(order.id,self.owner,'D5','D5-12345',order.revision,uuid4());confirm_receipt(self.member,order.id)
        self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,8)
        self.assertFalse(BenefitLedger.objects.filter(order_id=order.id,kind='EARN').exists())

    def test_points_insufficiency_rolls_back_inventory_order_and_reservations(self):
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        from orders.service import OrderError
        from inventory.models import InventoryLedger,InventoryReservation
        from benefits.models import PointsReservation
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=600,status='ON_SALE')
        quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':2})
        self.assertFalse(quote['ready']);self.assertEqual(quote['blockedCode'],'POINTS_UNAVAILABLE')
        with self.assertRaises(OrderError):submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        self.assertFalse(Order.objects.exists());self.assertFalse(InventoryLedger.objects.exists())
        self.assertFalse(InventoryReservation.objects.exists());self.assertFalse(PointsReservation.objects.exists())
        self.balance.refresh_from_db();self.assertEqual((self.balance.on_hand_base_units,self.balance.reserved_base_units),(10,0))

    def test_price_and_availability_revision_require_new_quote(self):
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        from orders.service import OrderError
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=100,status='ON_SALE')
        quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        offer.points_price=110;offer.revision+=1;offer.save(update_fields=['points_price','revision'])
        with self.assertRaises(OrderError) as caught:submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        self.assertEqual(caught.exception.code,'QUOTE_CHANGED')
        quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        offer.status='OFF_SALE';offer.revision+=1;offer.save(update_fields=['status','revision'])
        with self.assertRaises(OrderError) as caught:submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        self.assertEqual(caught.exception.code,'EXCHANGE_OFFER_UNAVAILABLE')

    def test_gate_closed_and_different_quote_id_same_key_conflicts(self):
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        from orders.service import OrderError
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=100,status='ON_SALE')
        first=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        with override_settings(EXCHANGE_ORDER_ENABLED=False):
            with self.assertRaises(OrderError) as caught:submit_exchange_order(self.member,{'quoteId':first['quoteId']},uuid4())
            self.assertEqual(caught.exception.code,'EXCHANGE_NOT_READY')
        key=uuid4();submit_exchange_order(self.member,{'quoteId':first['quoteId']},key)
        second=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        with self.assertRaises(OrderError) as caught:submit_exchange_order(self.member,{'quoteId':second['quoteId']},key)
        self.assertEqual(caught.exception.code,'IDEMPOTENCY_CONFLICT')

    def test_exchange_order_and_quote_proofs_are_immutable(self):
        from django.db import DatabaseError,transaction,connection
        from benefits.models import PointsReservation
        from points_exchange.models import ExchangeQuote
        order,line,_=self.exchange()
        for mutate in [lambda:Order.objects.filter(pk=order.id).update(order_kind='CASH'),
                       lambda:order.lines.update(points_unit_price=999,points_total=1998),
                       lambda:PointsReservation.objects.filter(order_id=order.id).update(amount=1),
                       lambda:ExchangeQuote.objects.filter(pk=order.quote_id).update(snapshot={})]:
            with self.assertRaises(DatabaseError),transaction.atomic():mutate()
        from payments.models import PaymentReceipt
        with self.assertRaises(DatabaseError),transaction.atomic():
            PaymentReceipt.objects.create(order=order,order_no=order.order_no,channel='OFFLINE',merchant_account_id='synthetic',
                external_trade_no='synthetic',amount_fen=1,paid_at=timezone.now(),source='OFFLINE_RECONCILIATION')

    def test_exchange_is_independent_of_cash_prices_switch_and_ratio(self):
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        from benefits.models import PointsPolicy
        self.sku.sale_status='OFF_SALE';self.sku.save(update_fields=['sale_status'])
        self.sku.product.status='OFF_SALE';self.sku.product.save(update_fields=['status'])
        PointsPolicy.objects.update_or_create(pk=1,defaults={'deduct_points':987,'deduct_fen':5,'max_percent':1})
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=101,status='ON_SALE')
        quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        data,_=submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        self.assertEqual((data['exchangePoints'],data['pointsDiscountFen'],data['payableFen']),(101,0,0))

    def test_unshipped_cancel_restores_points_and_stock_through_approved_case(self):
        from aftersales.service import apply_case
        order,line,_=self.exchange(2,'SHIP')
        case=apply_case(self.member,line.id,'REFUND_ONLY',2,'合成未发货取消',uuid4())
        self.finish_case(case)
        self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,10)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,1000)

    def test_return_acceptance_partial_approval_returns_only_approved_points(self):
        from fulfillment.models import Carrier
        from fulfillment.service import ship_order
        from aftersales.service import apply_case,review_case
        from aftersales.returns import accept_return,preview_return_acceptance
        from aftersales.api_read import case_data
        order,line,_=self.exchange(3,'SHIP')
        Carrier.objects.create(code='D5',name='合成物流',enabled=True)
        ship_order(order.id,self.owner,'D5','D5RET123',order.revision,uuid4())
        case=apply_case(self.member,line.id,'RETURN_REFUND',2,'合成发货后退货',uuid4())
        review_case(case.id,self.owner,True,'合成批准退货',case.revision);case.refresh_from_db()
        body={'expectedRevision':case.revision,'mode':'RECEIVED','receivedQuantity':2,'salableQuantity':1,
              'refundQuantity':1,'refundAmountFen':None,'reason':'合成验收一件退款'}
        self.assertEqual(preview_return_acceptance(case.id,self.owner,body)['refundPoints'],101)
        accept_return(case.id,self.owner,body,uuid4());case.refresh_from_db()
        from unittest.mock import patch
        from django.test import RequestFactory
        from payments.benefit_settlements import settle_benefits
        request=RequestFactory().post('/');request.request_id=uuid4()
        with patch('payments.benefit_settlements.confirm_action',return_value=None):
            done,_,_=settle_benefits(request,self.owner,case.id,{'expectedRevision':case.revision},uuid4())
        data=case_data(done)
        self.assertEqual((data['requestedRefundPoints'],data['pointsToReturn'],data['returnedPoints']),(202,101,101))
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points,798)
        self.balance.refresh_from_db();self.assertEqual(self.balance.on_hand_base_units,8)

    def test_expired_original_points_refund_receives_snapshotted_short_validity(self):
        from unittest.mock import patch
        from benefits.models import PointsGrant
        from aftersales.service import apply_case
        at=timezone.now();order,line,_=self.exchange(1)
        with patch('django.utils.timezone.now',return_value=at+timedelta(days=11)):
            case=apply_case(self.member,line.id,'REFUND_ONLY',1,'合成过期原积分退回',uuid4())
            self.finish_case(case)
        returned=PointsGrant.objects.get(source_ref__startswith='return:'+str(order.id))
        self.assertEqual(returned.original_points,101)
        self.assertGreater(returned.expires_at,at+timedelta(days=40))
        self.assertLess(returned.expires_at,at+timedelta(days=42))

    def test_sql_proof_cannot_be_moved_deleted_or_faked_without_immutable_events(self):
        from django.db import DatabaseError,transaction,connection
        from benefits.models import PointsReservation
        from inventory.models import InventoryReservation
        order,line,_=self.exchange()
        for mutate in [lambda:PointsReservation.objects.filter(order_id=order.id).update(order_id=uuid4()),
                       lambda:InventoryReservation.objects.filter(order_line=line).update(status='ACTIVE'),
                       lambda:InventoryReservation.objects.filter(order_line=line).delete()]:
            with self.assertRaises(DatabaseError),transaction.atomic():mutate()
        with self.assertRaises(DatabaseError),transaction.atomic():
            fake=Order.objects.create(order_no='PFAKE',order_kind='POINTS',member=self.member,quote_id=uuid4(),payment_method='POINTS',
                goods_total_fen=0,shipping_fee_fen=0,points_to_use=1,payable_fen=0,expires_at=timezone.now()+timedelta(minutes=10),
                status='PAID',paid_at=timezone.now())
            with connection.cursor() as cursor:cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')

    def test_failure_after_consumption_and_final_session_recheck_rolls_back_every_effect(self):
        from unittest.mock import patch
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        from inventory.models import InventoryReservation,InventoryLedger
        from benefits.models import PointsReservation,PointsEvent
        from orders.service import OrderError
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=100,status='ON_SALE')
        quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        def revoke():raise OrderError('合成会话撤销','SESSION_EXPIRED',401)
        for fault in ['voucher','session']:
            if fault=='voucher':
                with patch('fulfillment.service.issue_paid_vouchers_locked',side_effect=RuntimeError('synthetic fault')):
                    with self.assertRaises(RuntimeError):submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
            else:
                with self.assertRaises(OrderError):submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4(),authorize=__import__('unittest').mock.Mock(side_effect=[None,OrderError('合成撤销','SESSION_EXPIRED',401)]))
            self.assertFalse(Order.objects.exists());self.assertFalse(InventoryReservation.objects.exists())
            self.assertFalse(InventoryLedger.objects.exists());self.assertFalse(PointsReservation.objects.exists())
            self.assertFalse(PointsEvent.objects.filter(kind='CONSUME').exists())
            self.balance.refresh_from_db();self.assertEqual((self.balance.on_hand_base_units,self.balance.reserved_base_units),(10,0))
            account=PointsAccount.objects.get(member=self.member);self.assertEqual((account.settled_points,account.frozen_points),(1000,0))

    def test_matching_fake_consumed_reservations_without_events_are_rejected(self):
        from django.db import DatabaseError,transaction,connection
        from benefits.models import PointsReservation,PointsGrant,PointsEvent
        from inventory.models import InventoryReservation
        from orders.models import OrderLine
        for add_points_event in [False,True]:
            with self.assertRaisesRegex(DatabaseError,'exact immutable consumption events'),transaction.atomic():
                fake=Order.objects.create(order_no='PFAKE'+uuid4().hex,order_kind='POINTS',member=self.member,quote_id=uuid4(),payment_method='POINTS',
                    goods_total_fen=0,shipping_fee_fen=0,points_to_use=100,payable_fen=0,expires_at=timezone.now()+timedelta(minutes=10),
                    status='PAID',paid_at=timezone.now())
                unit=self.sku.current_unit
                line=OrderLine.objects.create(order=fake,sku=self.sku,product_id=self.sku.product_id,warehouse=self.balance.warehouse,
                    unit_version=unit,product_name='合成伪证明',sku_code=self.sku.sku_code,specs_snapshot=[],fulfillment_kind='REDEEM',
                    redeem_valid_until=self.sku.product.redeem_valid_until,base_unit=unit.base_unit,sale_unit=unit.sale_unit,ratio=unit.ratio,
                    quantity=1,base_quantity=unit.ratio,points_unit_price=100,points_total=100,unit_price_fen=0,goods_amount_fen=0,payable_fen=0)
                grant=PointsGrant.objects.get(member=self.member)
                PointsReservation.objects.create(member=self.member,grant=grant,order_id=fake.id,amount=100,status='CONSUMED')
                InventoryReservation.objects.create(order_line=line,balance=self.balance,base_quantity=1,status='CONSUMED',consumed_at=timezone.now())
                if add_points_event:PointsEvent.objects.create(member=self.member,grant=grant,order_id=fake.id,amount=100,kind='CONSUME')
                with connection.cursor() as cursor:cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')

    def test_consumed_proof_cannot_move_to_real_cash_order_and_null_receipt_cannot_attach(self):
        from django.db import DatabaseError,transaction
        from benefits.models import PointsReservation
        from payments.models import PaymentReceipt
        order,line,_=self.exchange()
        with override_settings(ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE':True,'WECHAT':True}):
            cash=payment_flow.PaymentFlowTests._order(self)
        with self.assertRaises(DatabaseError),transaction.atomic():
            PointsReservation.objects.filter(order_id=order.id).update(order_id=cash.id)
        receipt=PaymentReceipt.objects.create(order=None,order_no='synthetic-null',channel='OFFLINE',merchant_account_id='synthetic',
            external_trade_no='synthetic-null',amount_fen=1,paid_at=timezone.now(),source='OFFLINE_RECONCILIATION')
        with self.assertRaises(DatabaseError),transaction.atomic():
            PaymentReceipt.objects.filter(pk=receipt.id).update(order_id=order.id)

    def test_expired_quote_changed_address_and_default_warehouse_require_new_quote(self):
        from unittest.mock import patch
        from points_exchange.models import ExchangeOffer
        from points_exchange.orders import create_exchange_quote,submit_exchange_order
        from customers.models import CustomerAddress
        from orders.service import OrderError
        offer=ExchangeOffer.objects.create(sku=self.sku,points_price=100,status='ON_SALE')
        quote=create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        with patch('points_exchange.orders.timezone.now',return_value=timezone.now()+timedelta(minutes=11)):
            with self.assertRaises(OrderError) as caught:submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
            self.assertEqual(caught.exception.code,'QUOTE_EXPIRED')
        product=self.sku.product;product.fulfillment_kind='SHIP';product.save(update_fields=['fulfillment_kind'])
        with self.assertRaises(OrderError) as caught:create_exchange_quote(self.member,{'offerId':str(offer.id),'quantity':1})
        self.assertEqual(caught.exception.code,'ADDRESS_REQUIRED')
        address=CustomerAddress.objects.create(member=self.member,recipient_name='合成收件',phone='13800000000',province='浙江',city='杭州',district='西湖',detail='合成A')
        body={'offerId':str(offer.id),'quantity':1,'addressId':str(address.id)};quote=create_exchange_quote(self.member,body)
        address.detail='合成B';address.save(update_fields=['detail'])
        with self.assertRaises(OrderError) as caught:submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        self.assertEqual(caught.exception.code,'QUOTE_CHANGED')
        quote=create_exchange_quote(self.member,body)
        self.balance.warehouse.enabled=False;self.balance.warehouse.save(update_fields=['enabled'])
        with self.assertRaises(OrderError) as caught:submit_exchange_order(self.member,{'quoteId':quote['quoteId']},uuid4())
        self.assertEqual(caught.exception.code,'WAREHOUSE_CHANGED');self.assertFalse(Order.objects.exists())
