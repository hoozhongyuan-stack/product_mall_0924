"""Store handoff evidence must not be confused with voucher redemption."""
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from fulfillment.store_service import pickup_code, local_status, physical_handoff_fact


class StoreDeliveryReadTests(SimpleTestCase):
    def test_pickup_code_is_strong_and_domain_separated(self):
        import uuid
        from fulfillment.codes import voucher_code
        identity = uuid.uuid4()
        self.assertEqual(len(pickup_code(identity, 'nonce')), 26)
        self.assertNotEqual(pickup_code(identity, 'nonce'), voucher_code(identity, 'nonce'))
        self.assertNotEqual(pickup_code(identity, 'nonce'), pickup_code(identity, 'other'))

    def test_waiting_states_follow_mode(self):
        self.assertEqual(local_status('PICKUP', None), 'WAITING_PREPARATION')
        self.assertEqual(local_status('DELIVERY', SimpleNamespace(status='READY')), 'WAITING_DELIVERY')
        self.assertEqual(local_status('PICKUP', SimpleNamespace(status='READY')), 'WAITING_PICKUP')
        self.assertEqual(local_status('DELIVERY', SimpleNamespace(status='IN_TRANSIT')), 'IN_TRANSIT')

    def test_preparation_does_not_mark_handoff(self):
        with patch('fulfillment.store_service.Shipment.objects') as shipment, patch('fulfillment.store_service.StoreDelivery.objects') as delivery:
            shipment.filter.return_value.first.return_value = None
            delivery.filter.return_value.first.return_value = SimpleNamespace(status='READY', completed_at=None)
            self.assertIsNone(physical_handoff_fact(SimpleNamespace(id='order')))
            row = SimpleNamespace(status='IN_TRANSIT', completed_at=None)
            delivery.filter.return_value.first.return_value = row
            self.assertIs(physical_handoff_fact(SimpleNamespace(id='order')), row)


from django.test import TransactionTestCase, override_settings
from fulfillment.tests import test_flow as flow
from inventory.models import InventoryLedger
from orders.models import Order
from customers.models import Member
from .test_flow import payment_flow
from fulfillment.store_service import operate, delivery_data
from fulfillment.service import FulfillmentError, ship_order
from stores.models import Store, StoreStaff
from stores.access import StoreError
import uuid


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class StoreDeliveryFlowTests(TransactionTestCase):
    _order = payment_flow.PaymentFlowTests._order
    _evidence = payment_flow.PaymentFlowTests._evidence
    _paid_order = flow.FulfillmentFlowTests._paid_order

    def setUp(self):
        flow.FulfillmentFlowTests.setUp(self)
        self.store = Store.objects.create(warehouse=self.balance.warehouse, name='测试门店', contact_name='店长', contact_phone='13800000000', address='测试路', supported_modes=['PICKUP','DELIVERY','EXPRESS'],latitude=30,longitude=120,delivery_radius_meters=5000,delivery_fee_fen=0)
        StoreStaff.objects.create(store=self.store, member=self.member, permissions=['orders'])

    def local_order(self, mode, paid=True):
        from checkout.service import create_quote
        from orders.service import submit_order
        from orders.models import OrderLine
        from stores.models import StoreProduct
        from customers.models import CustomerAddress
        from payments.service import record_verified_payment
        product=self.sku.product
        product.fulfillment_kind='SHIP'
        product.save(update_fields=['fulfillment_kind'])
        StoreProduct.objects.get_or_create(store=self.store,product=product,defaults={'on_sale':True})
        body={'items':[{'skuId':str(self.sku.id),'quantity':2}],'storeId':str(self.store.id),'deliveryMode':mode}
        if mode!='PICKUP':
            address=CustomerAddress.objects.create(member=self.member,recipient_name='测试收件人',phone='13800000000',province='浙江',city='杭州',district='西湖区',detail='测试路',latitude=30,longitude=120)
            body['addressId']=str(address.id)
        quote=create_quote(body,self.member)
        data,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid.uuid4())
        order=Order.objects.get(pk=data['orderId'])
        if paid:
            record_verified_payment(self._evidence(order,trade_no=uuid.uuid4().hex,event_id=uuid.uuid4().hex))
        order.refresh_from_db()
        return order,OrderLine.objects.get(order=order)

    def action(self, order, action, **kwargs):
        order.refresh_from_db()
        body = {'action':action,'expectedRevision':order.revision, **kwargs}
        return operate(self.member,self.store.id,order.id,body,uuid.uuid4())

    def test_pickup_requires_customer_code_and_does_not_debit_twice(self):
        order, line = self.local_order('PICKUP')
        self.action(order,'PREPARE')
        self.assertIsNone(physical_handoff_fact(order))
        self.assertNotIn('pickupCode',delivery_data(order))
        code = delivery_data(order,include_code=True)['pickupCode']
        with self.assertRaises(FulfillmentError):
            self.action(order,'COMPLETE',pickupCode='wrong')
        self.action(order,'COMPLETE',pickupCode=code)
        self.assertEqual(physical_handoff_fact(order).status,'COMPLETED')
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.on_hand_base_units,8)
        self.assertEqual(InventoryLedger.objects.filter(order_line=line,movement_type='SALE').count(),1)
        from inventory.refunds import restore_unshipped_refund_locked
        from django.db import transaction
        with transaction.atomic(), self.assertRaises(ValueError):
            restore_unshipped_refund_locked(line,1,uuid.uuid4())

    def test_delivery_has_sequence_evidence_and_replay_guards(self):
        order, _ = self.local_order('DELIVERY')
        with self.assertRaises(FulfillmentError):
            self.action(order,'DISPATCH')
        self.action(order,'PREPARE')
        order.refresh_from_db()
        body={'action':'DISPATCH','expectedRevision':order.revision}
        key=uuid.uuid4()
        operate(self.member,self.store.id,order.id,body,key)
        operate(self.member,self.store.id,order.id,body,key)
        with self.assertRaises(FulfillmentError):
            operate(self.member,self.store.id,order.id,{**body,'note':'changed'},key)
        self.assertEqual(physical_handoff_fact(order).status,'IN_TRANSIT')
        with self.assertRaises(FulfillmentError):
            self.action(order,'COMPLETE')
        self.action(order,'COMPLETE',note='已当面交付顾客')
        from fulfillment.read import completion_fact, fulfillment_data
        self.assertTrue(completion_fact(order))
        self.assertEqual(fulfillment_data(order)['fulfillmentStatus'],'COMPLETED')

    def test_cross_store_staff_and_express_path_are_guarded(self):
        order,_=self.local_order('PICKUP')
        other=Member.objects.create(wechat_app_id='wx-payment-test',wechat_openid='outsider',grade=self.member.grade)
        with self.assertRaises(StoreError):
            operate(other,self.store.id,order.id,{'action':'PREPARE','expectedRevision':order.revision},uuid.uuid4())
        from fulfillment.models import Carrier
        Carrier.objects.create(code='SF',name='测试快递',enabled=True)
        with self.assertRaises(FulfillmentError):
            ship_order(order.id,self.owner,'SF','SF123456',order.revision,uuid.uuid4())
        express,_=self.local_order('EXPRESS')
        self.action(express,'SHIP',carrierCode='SF',trackingNo='SF123456')
        fact=physical_handoff_fact(express)
        self.assertEqual(fact.shipped_by_member_id,self.member.id)
        self.assertIsNone(fact.shipped_by_id)

    def test_handoff_blocks_refund_only(self):
        order,line=self.local_order('PICKUP')
        self.action(order,'PREPARE')
        self.action(order,'COMPLETE',pickupCode=delivery_data(order,include_code=True)['pickupCode'])
        from aftersales.service import _eligibility, AfterSaleError
        with self.assertRaises(AfterSaleError) as caught:
            _eligibility(order,line,'REFUND_ONLY','UNUSED',1)
        self.assertEqual(caught.exception.code,'RETURN_REQUIRED')

    def test_store_task_filters_are_distinct_from_express(self):
        from fulfillment.task_reads import filter_order_tasks
        order,_=self.local_order('DELIVERY')
        self.assertTrue(filter_order_tasks(Order.objects.all(),'WAITING_PREPARATION').filter(pk=order.pk).exists())
        self.assertFalse(filter_order_tasks(Order.objects.all(),'WAITING_SHIPMENT').filter(pk=order.pk).exists())
        self.action(order,'PREPARE')
        self.assertTrue(filter_order_tasks(Order.objects.all(),'WAITING_DELIVERY').filter(pk=order.pk).exists())
        self.action(order,'DISPATCH')
        self.assertTrue(filter_order_tasks(Order.objects.all(),'DELIVERING').filter(pk=order.pk).exists())
        from fulfillment.read import fulfillment_data
        self.assertEqual(fulfillment_data(order)['fulfillmentStatus'],'DELIVERING')

    def completed_pickup_return(self):
        from aftersales.service import apply_case,review_case
        from aftersales.returns import accept_return
        order,line=self.local_order('PICKUP')
        self.action(order,'PREPARE')
        self.action(order,'COMPLETE',pickupCode=delivery_data(order,include_code=True)['pickupCode'])
        case=apply_case(self.member,line.id,'RETURN_REFUND',1,'门店自提商品退货申请',uuid.uuid4())
        case=review_case(case.id,self.owner,True,'同意退货后验收',case.revision)
        body={'expectedRevision':case.revision,'mode':'RECEIVED','receivedQuantity':1,'salableQuantity':1,'refundQuantity':1,'refundAmountFen':None,'reason':'门店自提退货验收通过'}
        key=uuid.uuid4()
        accept_return(case.id,self.owner,body,key)
        accept_return(case.id,self.owner,body,key)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.on_hand_base_units,9)
        self.assertEqual(InventoryLedger.objects.filter(return_order_line=line,movement_type='RETURN').count(),1)

    def test_staff_aftersale_receipt_is_fact_only_and_replays(self):
        from aftersales.service import apply_case,review_case
        from aftersales.store_service import record_note,notes_data
        order,line=self.local_order('PICKUP')
        self.action(order,'PREPARE')
        self.action(order,'COMPLETE',pickupCode=delivery_data(order,include_code=True)['pickupCode'])
        case=apply_case(self.member,line.id,'RETURN_REFUND',1,'门店自提商品退货申请',uuid.uuid4())
        case=review_case(case.id,self.owner,True,'同意退货后验收',case.revision)
        body={'expectedRevision':case.revision,'kind':'RECEIPT','receivedQuantity':1,'salableQuantity':1,'note':'已收到顾客退回商品，等待平台复核'}
        key=uuid.uuid4()
        row=record_note(self.member,self.store.id,case.id,body,key)
        self.assertEqual(row.id,record_note(self.member,self.store.id,case.id,body,key).id)
        self.balance.refresh_from_db();case.refresh_from_db()
        self.assertEqual(self.balance.on_hand_base_units,8)
        self.assertEqual(case.status,'WAITING_RETURN')
        self.assertTrue(notes_data(case)[0]['platformReviewRequired'])

    def test_store_order_and_aftersale_api_requires_live_staff(self):
        from django.test import Client
        from customers.models import MemberSession
        order,line=self.local_order('PICKUP')
        client=Client()
        path=f'/api/v1/app/store-center/stores/{self.store.id}/orders/{order.id}'
        self.assertEqual(client.get(path).status_code,401)
        token,_=MemberSession.issue(self.member)
        headers={'HTTP_AUTHORIZATION':f'Bearer {token}'}
        self.assertEqual(client.get(path,**headers).status_code,200)
        self.assertEqual(client.get(path,**headers).json()['data']['orderId'],str(order.id))
        listpath=f'/api/v1/app/store-center/stores/{self.store.id}/orders'
        listing=client.get(listpath,{'fulfillment':'WAITING_PREPARATION'},**headers)
        self.assertEqual(listing.status_code,200,listing.content)
        self.assertEqual(listing.json()['data']['total'],1)
        self.assertEqual(client.get(listpath,{'fulfillment':'INVALID'},**headers).status_code,400)
        self.assertEqual(client.get(listpath,{'page':'bad'},**headers).status_code,400)
        self.assertEqual(client.get(listpath,{'page':'0'},**headers).status_code,400)
        self.assertEqual(client.get(listpath,{'status':'PAID'},**headers).status_code,200)
        self.assertEqual(client.get(listpath,{'status':'INVALID'},**headers).status_code,400)
        import json
        result=client.post(path+'/fulfillment',data=json.dumps({'action':'PREPARE','expectedRevision':order.revision}),content_type='application/json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),**headers)
        self.assertEqual(result.status_code,200,result.content)
        self.assertNotIn('pickupCode',result.json()['data']['storeDelivery'])
        from aftersales.service import apply_case
        case=apply_case(self.member,line.id,'REFUND_ONLY',1,'门店商品申请仅退款',uuid.uuid4())
        casepath=f'/api/v1/app/store-center/stores/{self.store.id}/aftersales/{case.id}'
        self.assertEqual(client.get(casepath,**headers).status_code,200)
        afterlist=casepath.rsplit('/',1)[0]
        self.assertEqual(client.get(afterlist,**headers).json()['data']['total'],1)
        self.assertEqual(client.get(afterlist,{'page':'bad'},**headers).status_code,400)
        self.assertEqual(client.get(afterlist,{'page':'0'},**headers).status_code,400)
        result=client.post(casepath+'/notes',data=json.dumps({'kind':'ADVICE','expectedRevision':case.revision,'note':'建议平台核实顾客退款诉求'}),content_type='application/json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),**headers)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(len(result.json()['data']['storeNotes']),1)
        StoreStaff.objects.filter(store=self.store,member=self.member).update(enabled=False)
        self.assertEqual(client.get(path,**headers).status_code,403)
        self.assertEqual(client.get(casepath,**headers).status_code,403)

    def test_unpaid_stale_and_aftersale_holds_block_handoff(self):
        unpaid,_=self.local_order('PICKUP',paid=False)
        with self.assertRaises(FulfillmentError) as caught:
            self.action(unpaid,'PREPARE')
        self.assertEqual(caught.exception.code,'ORDER_NOT_PAID')
        order,line=self.local_order('PICKUP')
        with self.assertRaises(FulfillmentError):
            operate(self.member,self.store.id,order.id,{'action':'PREPARE','expectedRevision':0},uuid.uuid4())
        from aftersales.service import apply_case
        apply_case(self.member,line.id,'REFUND_ONLY',1,'暂停履约等待退款审核',uuid.uuid4())
        with self.assertRaises(FulfillmentError) as caught:
            self.action(order,'PREPARE')
        self.assertEqual(caught.exception.code,'AFTERSALE_BLOCKS_SHIPMENT')

    def test_closed_actions_and_malformed_input_do_not_create_facts(self):
        order,_=self.local_order('PICKUP')
        for body,key in [({},uuid.uuid4()),({'action':'UNKNOWN','expectedRevision':order.revision},uuid.uuid4()),({'action':'PREPARE','expectedRevision':order.revision},'bad')]:
            with self.assertRaises(FulfillmentError):
                operate(self.member,self.store.id,order.id,body,key)
        self.action(order,'PREPARE')
        with self.assertRaises(FulfillmentError):
            self.action(order,'PREPARE')
        with self.assertRaises(FulfillmentError):
            self.action(order,'DISPATCH')
        self.action(order,'COMPLETE',pickupCode=delivery_data(order,include_code=True)['pickupCode'])
        with self.assertRaises(FulfillmentError):
            self.action(order,'COMPLETE',pickupCode='invalid')

    def test_completed_pickup_return_uses_original_stock_once(self):
        self.completed_pickup_return()

    def test_completed_alias_pickup_return_restores_pool_anchor_once(self):
        from inventory.tests import test_stock_pool_cross_domain as pool_fixture
        anchor,alias=pool_fixture.SharedPoolReturnTests._alias(self)
        self.sku=alias
        self.completed_pickup_return()
        self.assertEqual(self.balance.sku_id,anchor.id)
        self.assertFalse(type(self.balance).objects.filter(sku=alias).exists())
