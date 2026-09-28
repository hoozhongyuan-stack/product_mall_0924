"""D3 synthetic funds amount components and non-funds completion."""
import uuid
from django.test import override_settings, TransactionTestCase
from . import test_refund_flow as refund_flow
from aftersales.service import apply_case, review_case
from payments.refunds import prepare_refund

@override_settings(WECHAT_MINI_APP_ID="wx-payment-test",ORDER_PAYMENT_METHODS_ENABLED={"OFFLINE":True,"WECHAT":True})
class D3SettlementTests(TransactionTestCase):
    setUp = refund_flow.RefundFlowTests.setUp
    _order = refund_flow.RefundFlowTests._order
    _paid_order = refund_flow.RefundFlowTests._paid_order
    _evidence = refund_flow.RefundFlowTests._evidence
    refund_evidence = refund_flow.RefundFlowTests.refund_evidence
    def test_full_unshipped_refund_includes_shipping_once(self):
        from shipping.models import ShippingPolicy
        ShippingPolicy.objects.update(fee_fen=600)
        order,line=self._paid_order("SHIP")
        self.assertEqual(order.shipping_fee_fen,600)
        case=apply_case(self.member,line.id,"REFUND_ONLY",2,"合成整项退款",uuid.uuid4())
        review_case(case.id,self.owner,True,"合成同意退款",case.revision)
        intent=prepare_refund(case.id,self.owner,uuid.uuid4())
        self.assertEqual(intent.amount_fen,line.payable_fen+600)
        self.assertEqual(self.refund_evidence(intent).amount_fen,order.payable_fen)
        from payments.refunds import record_verified_refund
        self.assertEqual(record_verified_refund(self.refund_evidence(intent))["outcome"],"SUCCEEDED")

    def test_partial_unshipped_has_no_shipping(self):
        from shipping.models import ShippingPolicy
        ShippingPolicy.objects.update(fee_fen=600)
        order,line=self._paid_order("SHIP")
        case=apply_case(self.member,line.id,"REFUND_ONLY",1,"合成部分退款",uuid.uuid4())
        review_case(case.id,self.owner,True,"合成同意退款",case.revision)
        intent=prepare_refund(case.id,self.owner,uuid.uuid4())
        self.assertEqual(intent.amount_fen,line.payable_fen//2)

    def _zero_order(self):
        from benefits.models import CouponCampaign,MemberCoupon
        from django.utils import timezone
        from datetime import timedelta
        now=timezone.now()
        campaign=CouponCampaign.objects.create(code="ZERO",title="合成全额券",kind="CASH",discount_fen=2000,
            redeem_eligible=True,valid_from=now-timedelta(days=1),valid_until=now+timedelta(days=1))
        coupon=MemberCoupon.objects.create(member=self.member,campaign=campaign)
        order=self._order(coupon_id=coupon.id)
        from orders.models import OrderLine
        return order,OrderLine.objects.get(order=order)

    def test_zero_cash_requires_authorization_and_never_fabricates_money(self):
        from unittest.mock import patch
        from django.test import RequestFactory
        from payments.benefit_settlements import settle_benefits
        from aftersales.models import BenefitOnlySettlement,AfterSaleCase
        from payments.models import RefundEvidence,RefundIntent
        from fulfillment.models import RedeemVoucher
        order,line=self._zero_order()
        self.assertEqual(order.payable_fen,0)
        case=apply_case(self.member,line.id,"REFUND_ONLY",2,"合成零现金退款",uuid.uuid4())
        review_case(case.id,self.owner,True,"合成同意退款",case.revision)
        case.refresh_from_db()
        request=RequestFactory().post("/")
        request.request_id=uuid.uuid4()
        key=uuid.uuid4()
        with patch("payments.benefit_settlements.confirm_action",return_value=None) as confirm:
            done,replay,denied=settle_benefits(request,self.owner,case.id,{"expectedRevision":case.revision},key)
            self.assertEqual(done.status,"COMPLETED")
            self.assertFalse(replay)
            confirm.assert_called_once()
            done,replay,denied=settle_benefits(request,self.owner,case.id,{"expectedRevision":case.revision},key)
            self.assertTrue(replay)
            self.assertEqual(confirm.call_count,1)
        self.assertEqual(BenefitOnlySettlement.objects.count(),1)
        self.assertFalse(RefundEvidence.objects.exists())
        self.assertFalse(RefundIntent.objects.exists())
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity,2)

    def test_cash_refund_cannot_use_benefit_completion(self):
        from unittest.mock import patch
        from django.test import RequestFactory
        from payments.benefit_settlements import settle_benefits
        from payments.refunds import RefundError
        order,line=self._paid_order()
        case=apply_case(self.member,line.id,"REFUND_ONLY",1,"合成现金退款",uuid.uuid4())
        review_case(case.id,self.owner,True,"合成同意退款",case.revision)
        case.refresh_from_db()
        with patch("payments.benefit_settlements.confirm_action",return_value=None):
            with self.assertRaises(RefundError) as raised:
                settle_benefits(RequestFactory().post("/"),self.owner,case.id,{"expectedRevision":case.revision},uuid.uuid4())
            self.assertEqual(raised.exception.code,"FUNDS_REFUND_REQUIRED")

    def test_rejected_and_withdrawn_do_not_entitle_shipping(self):
        from aftersales.service import withdraw_case
        from aftersales.models import OrderShippingRefundClaim
        order,line=self._paid_order("SHIP")
        rejected=apply_case(self.member,line.id,"REFUND_ONLY",2,"合成拒绝申请",uuid.uuid4())
        review_case(rejected.id,self.owner,False,"合成拒绝审核",rejected.revision)
        withdrawn=apply_case(self.member,line.id,"REFUND_ONLY",2,"合成撤销申请",uuid.uuid4())
        withdraw_case(withdrawn.id,self.member,withdrawn.revision)
        self.assertFalse(OrderShippingRefundClaim.objects.exists())

    def test_last_partial_claims_shipping_after_previous_success(self):
        from payments.refunds import record_verified_refund
        from aftersales.models import OrderShippingRefundClaim
        order,line=self._paid_order("SHIP")
        first=apply_case(self.member,line.id,"REFUND_ONLY",1,"合成第一件退款",uuid.uuid4())
        review_case(first.id,self.owner,True,"合成批准第一件",first.revision)
        i1=prepare_refund(first.id,self.owner,uuid.uuid4())
        self.assertEqual(i1.amount_fen,line.payable_fen//2)
        self.assertEqual(record_verified_refund(self.refund_evidence(i1))["outcome"],"SUCCEEDED")
        last=apply_case(self.member,line.id,"REFUND_ONLY",1,"合成第二件退款",uuid.uuid4())
        review_case(last.id,self.owner,True,"合成批准第二件",last.revision)
        i2=prepare_refund(last.id,self.owner,uuid.uuid4())
        self.assertEqual(i2.amount_fen,line.payable_fen//2+order.shipping_fee_fen)
        self.assertEqual(OrderShippingRefundClaim.objects.count(),1)
        self.assertEqual(record_verified_refund(self.refund_evidence(i2))["outcome"],"SUCCEEDED")
        from payments.refund_amounts import shipping_refunded_fen
        self.assertEqual(shipping_refunded_fen(order.id),order.shipping_fee_fen)

    def test_zero_api_password_revision_and_replay(self):
        import json
        from aftersales.models import BenefitOnlySettlement
        order,line=self._zero_order()
        case=apply_case(self.member,line.id,"REFUND_ONLY",1,"合成零现金售后",uuid.uuid4())
        review_case(case.id,self.owner,True,"合成批准权益结算",case.revision)
        case.refresh_from_db()
        def post(url,body,**headers):
            return self.client.post(url,data=json.dumps(body),content_type="application/json",**headers)
        login=post('/api/v1/admin/auth/login',{'loginName':self.owner.login_name,'password':'Long test password 2026!'})
        self.assertEqual(login.status_code,200,login.content)
        url=f'/api/v1/admin/aftersales/{case.id}/settle-benefits'
        k=str(uuid.uuid4());body={'expectedRevision':case.revision}
        denied=post(url,body,HTTP_IDEMPOTENCY_KEY=k)
        self.assertEqual(denied.status_code,403,denied.content)
        self.assertFalse(BenefitOnlySettlement.objects.exists())
        confirmation=post('/api/v1/admin/auth/confirm',{'action':'refund.benefits.settle','objectId':str(case.id),
              'revision':case.revision,'password':'Long test password 2026!'})
        self.assertEqual(confirmation.status_code,200,confirmation.content)
        token=confirmation.json()['data']['confirmationToken']
        done=post(url,body,HTTP_IDEMPOTENCY_KEY=k,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(done.status_code,201,done.content)
        self.assertEqual(done.json()['data']['status'],'COMPLETED')
        replay=post(url,body,HTTP_IDEMPOTENCY_KEY=k)
        self.assertEqual(replay.status_code,200,replay.content)
        self.assertEqual(BenefitOnlySettlement.objects.count(),1)
        changed=post(url,{'expectedRevision':case.revision+1},HTTP_IDEMPOTENCY_KEY=k)
        self.assertEqual(changed.status_code,409,changed.content)

    def test_shipping_amount_components_are_immutable(self):
        from django.db import transaction,DatabaseError
        from aftersales.models import RefundAmountSnapshot,OrderShippingRefundClaim
        order,line=self._paid_order('SHIP')
        case=apply_case(self.member,line.id,'REFUND_ONLY',2,'合成冻结退款测试',uuid.uuid4())
        review_case(case.id,self.owner,True,'合成批准冻结金额',case.revision)
        with self.assertRaises(DatabaseError),transaction.atomic():
            RefundAmountSnapshot.objects.filter(case=case).update(shipping_fen=0,total_fen=line.payable_fen)
        with self.assertRaises(DatabaseError),transaction.atomic():
            OrderShippingRefundClaim.objects.filter(order=order).delete()

    def test_all_shipping_lines_must_be_approved_and_fee_refunded_once(self):
        from catalog.models import Sku,SkuUnitVersion
        from inventory.models import InventoryBalance,InventoryLedger
        from customers.models import CustomerAddress
        from checkout.service import create_quote
        from orders.service import submit_order
        from orders.models import Order
        from payments.service import record_verified_payment
        from payments.refunds import record_verified_refund
        from payments.models import RefundEvidence
        from aftersales.models import OrderShippingRefundClaim
        from django.db.models import Sum
        product=self.sku.product;product.fulfillment_kind='SHIP';product.save(update_fields=['fulfillment_kind'])
        second=Sku.objects.create(product=product,sku_code='D3-SECOND',spec_key='second',list_price_fen=1500,sale_status='ON_SALE')
        unit=SkuUnitVersion.objects.create(sku=second,base_unit='件',sale_unit='件',ratio=1)
        second.current_unit=unit;second.save(update_fields=['current_unit'])
        InventoryBalance.objects.create(warehouse=self.balance.warehouse,sku=second,on_hand_base_units=10)
        address=CustomerAddress.objects.create(member=self.member,recipient_name='合成收件人',phone='13800000000',province='浙江',city='杭州',district='西湖',detail='测试地址')
        quote=create_quote({'items':[{'skuId':str(self.sku.id),'quantity':2},{'skuId':str(second.id),'quantity':2}],'addressId':str(address.id)},self.member)
        data,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid.uuid4())
        order=Order.objects.get(pk=data['orderId'])
        record_verified_payment(self._evidence(order,trade_no=uuid.uuid4().hex,event_id=uuid.uuid4().hex))
        lines=list(order.lines.order_by('sku_id'))
        cases=[];intents=[]
        for line in lines:
            case=apply_case(self.member,line.id,'REFUND_ONLY',2,'合成多订单项退款',uuid.uuid4())
            review_case(case.id,self.owner,True,'合成批准整项退款',case.revision)
            cases.append(case);intents.append(prepare_refund(case.id,self.owner,uuid.uuid4()))
        self.assertEqual(intents[0].amount_fen,lines[0].payable_fen)
        self.assertEqual(intents[1].amount_fen,lines[1].payable_fen+order.shipping_fee_fen)
        self.assertEqual(OrderShippingRefundClaim.objects.count(),1)
        for intent in intents:
            evidence=self.refund_evidence(intent)
            self.assertEqual(record_verified_refund(evidence)['outcome'],'SUCCEEDED')
            self.assertEqual(record_verified_refund(evidence)['outcome'],'SUCCEEDED')
        self.assertEqual(RefundEvidence.objects.aggregate(total=Sum('amount_fen'))['total'],order.payable_fen)
        self.assertEqual(InventoryLedger.objects.filter(movement_type='REFUND').count(),2)
        self.assertEqual(list(InventoryBalance.objects.order_by('sku_id').values_list('on_hand_base_units',flat=True)),[10,10])

    def test_concurrent_zero_completion_has_one_consistent_result(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections
        from django.test import RequestFactory
        from unittest.mock import patch
        from payments.benefit_settlements import settle_benefits
        from aftersales.models import BenefitOnlySettlement
        from fulfillment.models import RedeemVoucher
        order,line=self._zero_order()
        case=apply_case(self.member,line.id,'REFUND_ONLY',1,'合成并发权益退款',uuid.uuid4())
        review_case(case.id,self.owner,True,'合成批准权益结算',case.revision);case.refresh_from_db()
        key=uuid.uuid4();revision=case.revision
        def worker():
            close_old_connections()
            try:
                request=RequestFactory().post('/');request.request_id=uuid.uuid4()
                _,replay,_=settle_benefits(request,self.owner,case.id,{'expectedRevision':revision},key)
                return replay
            finally: close_old_connections()
        with patch('payments.benefit_settlements.confirm_action',return_value=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                values=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual(sorted(values),[False,True])
        self.assertEqual(BenefitOnlySettlement.objects.count(),1)
        self.assertEqual(RedeemVoucher.objects.get(order_line=line).voided_quantity,1)

    def test_zero_goods_with_shipping_requires_real_funds(self):
        from benefits.models import CouponCampaign,MemberCoupon
        from django.utils import timezone
        from datetime import timedelta
        from customers.models import CustomerAddress
        from checkout.service import create_quote
        from orders.service import submit_order
        from orders.models import Order,OrderLine
        from payments.service import record_verified_payment
        from payments.benefit_settlements import settle_benefits
        from payments.refunds import RefundError,record_verified_refund
        from django.test import RequestFactory
        from aftersales.refund_amounts import amount_components
        product=self.sku.product;product.fulfillment_kind='SHIP';product.save(update_fields=['fulfillment_kind'])
        now=timezone.now()
        campaign=CouponCampaign.objects.create(code='ZERO-SHIP',title='合成全额商品券',kind='CASH',discount_fen=2000,
            valid_from=now-timedelta(days=1),valid_until=now+timedelta(days=1))
        coupon=MemberCoupon.objects.create(member=self.member,campaign=campaign)
        address=CustomerAddress.objects.create(member=self.member,recipient_name='合成收件人',phone='13800000000',province='浙江',city='杭州',district='西湖',detail='测试地址')
        quote=create_quote({'items':[{'skuId':str(self.sku.id),'quantity':2}],'addressId':str(address.id),'couponId':str(coupon.id)},self.member)
        data,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid.uuid4())
        order=Order.objects.get(pk=data['orderId']);line=OrderLine.objects.get(order=order)
        record_verified_payment(self._evidence(order,trade_no=uuid.uuid4().hex,event_id=uuid.uuid4().hex))
        self.assertEqual(line.payable_fen,0)
        case=apply_case(self.member,line.id,'REFUND_ONLY',2,'合成运费退款测试',uuid.uuid4())
        review_case(case.id,self.owner,True,'合成批准整项退款',case.revision);case.refresh_from_db()
        self.assertEqual(amount_components(case),{'goodsRefundAmountFen':0,'shippingRefundAmountFen':order.shipping_fee_fen,'totalRefundAmountFen':order.shipping_fee_fen})
        with self.assertRaises(RefundError) as raised:
            settle_benefits(RequestFactory().post('/'),self.owner,case.id,{'expectedRevision':case.revision},uuid.uuid4())
        self.assertEqual(raised.exception.code,'FUNDS_REFUND_REQUIRED')
        intent=prepare_refund(case.id,self.owner,uuid.uuid4())
        self.assertEqual(intent.amount_fen,order.payable_fen)
        self.assertEqual(record_verified_refund(self.refund_evidence(intent))['outcome'],'SUCCEEDED')
