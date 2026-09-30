"""C2 orchestration uses signed-adapter doubles, never the merchant network."""
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.db import connection, transaction, DatabaseError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from customers.models import MemberSession
from orders.models import Order
from orders.service import cancel_order
from payments.models import PaymentReceipt, PaymentAnomaly
from payments.tests import test_payment_flow as flow

@override_settings(WECHAT_MINI_APP_ID='wx-payment-test', ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class WechatFlowTests(TransactionTestCase):
    _order = flow.PaymentFlowTests._order
    def setUp(self):
        flow.PaymentFlowTests.setUp(self)
        self.gateway = Mock()
        self.gateway.config = SimpleNamespace(app_id='wx-payment-test', merchant_id='TEST-MCH')
        self.gateway.prepay.return_value = {'prepayId':'PREPAY-TEST','paymentParameters':{'timeStamp':'1','nonceStr':'nonce','package':'prepay_id=PREPAY-TEST','signType':'RSA','paySign':'signed'}}
        self.gateway.payment_parameters.return_value = self.gateway.prepay.return_value['paymentParameters']
        self.gateway.query.side_effect = self.notpay
        self.patch = patch('payments.wechat_service.get_gateway', return_value=self.gateway)
        self.patch.start();self.addCleanup(self.patch.stop)
    def notpay(self, out_trade_no):
        return {'appid':'wx-payment-test','mchid':'TEST-MCH','out_trade_no':out_trade_no,'trade_type':'JSAPI','trade_state':'NOTPAY','amount':{'total':2000,'currency':'CNY'},'payer':{'openid':'pay-buyer'}}
    def success(self, attempt, amount=2000, **changes):
        return {**self.notpay(attempt.out_trade_no), 'trade_state':'SUCCESS','transaction_id':'WX-TRADE-1','success_time':timezone.now().isoformat(),'amount':{'total':amount,'currency':'CNY'},**changes}
    def prepay(self, order, key=None):
        from payments.wechat_service import create_prepay
        return create_prepay(self.member, order.id, key or uuid.uuid4())
    def test_prepay_is_one_external_order_replay_and_network_outside_transaction(self):
        from payments.models import WechatPaymentAttempt
        order=self._order('WECHAT');key=uuid.uuid4()
        def external(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            return self.gateway.prepay.return_value
        self.gateway.prepay.side_effect=external
        first=self.prepay(order,key);second=self.prepay(order,key)
        self.assertEqual(first['attemptId'],second['attemptId']);self.assertEqual(self.gateway.prepay.call_count,1)
        self.assertEqual(len(WechatPaymentAttempt.objects.get().out_trade_no),32)
        self.assertFalse(PaymentReceipt.objects.exists());self.assertEqual(Order.objects.get(pk=order.id).status,'PENDING_PAYMENT')
    def test_lost_prepay_response_queries_before_reusing_same_external_order(self):
        from payments.wechat_gateway import WechatGatewayError
        from payments.wechat_service import create_prepay
        from payments.models import WechatPaymentAttempt
        from payments.service import PaymentError
        order=self._order('WECHAT');key=uuid.uuid4()
        self.gateway.prepay.side_effect=WechatGatewayError('支付服务暂不可用。','WECHAT_UNAVAILABLE',503)
        with self.assertRaises(PaymentError):create_prepay(self.member,order.id,key)
        out=WechatPaymentAttempt.objects.get().out_trade_no
        self.gateway.prepay.side_effect=None
        result=create_prepay(self.member,order.id,key)
        self.assertEqual(WechatPaymentAttempt.objects.get().out_trade_no,out);self.gateway.query.assert_called_once_with(out)
        self.assertIn('paymentParameters',result)
    def test_query_success_uses_total_and_settles_once(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import query_payment
        order=self._order('WECHAT');self.prepay(order);attempt=WechatPaymentAttempt.objects.get()
        response=self.success(attempt);response['amount']['payer_total']=1500
        self.gateway.query.return_value=response;self.gateway.query.side_effect=None
        self.assertEqual(query_payment(self.member,order.id)['paymentState'],'SUCCESS')
        self.assertEqual(query_payment(self.member,order.id)['order']['status'],'PAID')
        self.assertEqual(PaymentReceipt.objects.count(),1);self.assertEqual(PaymentReceipt.objects.get().amount_fen,2000)
    def test_notification_duplicates_amount_mismatch_and_closed_late_are_anomalies(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import handle_notification
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.verify_notification.return_value=('WX-EVENT-1',self.success(a,1999))
        self.assertEqual(handle_notification({},b'raw')['outcome'],'ANOMALY')
        handle_notification({},b'raw');self.assertEqual(PaymentReceipt.objects.count(),1)
        self.assertEqual(PaymentAnomaly.objects.get().reason,'AMOUNT_MISMATCH')
        cancel_order(self.member,order.id)
        self.gateway.verify_notification.return_value=('WX-EVENT-2',self.success(a,2000,transaction_id='WX-LATE'))
        handle_notification({},b'raw');self.assertEqual(Order.objects.get(pk=order.id).status,'CLOSED')
        self.assertTrue(PaymentAnomaly.objects.filter(reason='CLOSED_ORDER').exists())
    def test_forged_merchant_payer_currency_never_records_funds(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import handle_notification
        from payments.service import PaymentError
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        for changes in [{'mchid':'OTHER'},{'appid':'OTHER'},{'payer':{'openid':'OTHER'}},{'amount':{'total':2000,'currency':'USD'}},{'trade_state':[]}]:
            self.gateway.verify_notification.return_value=('WX-EVENT',self.success(a,**changes))
            with self.assertRaises(PaymentError):handle_notification({},b'raw')
        self.assertFalse(PaymentReceipt.objects.exists())
    def test_failed_settlement_retains_receipt_and_retry_recovers(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import handle_notification
        from payments.service import PaymentError
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.verify_notification.return_value=('WX-EVENT',self.success(a))
        with patch('orders.service.consume_order_reservations',side_effect=RuntimeError('test failure')):
            with self.assertRaises(PaymentError):handle_notification({},b'raw')
        self.assertEqual(PaymentReceipt.objects.count(),1)
        self.assertEqual(handle_notification({},b'raw')['outcome'],'PAID')
    def test_cancel_queues_remote_close_and_gateway_calls_never_hold_locks(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import reconcile_attempt
        order=self._order('WECHAT');self.prepay(order);cancel_order(self.member,order.id)
        a=WechatPaymentAttempt.objects.get();self.assertTrue(a.close_requested)
        def external(*args):self.assertFalse(connection.in_atomic_block)
        self.gateway.close.side_effect=external
        reconcile_attempt(a.id)
        self.gateway.close.assert_called_once_with(a.out_trade_no)
        self.assertEqual(Order.objects.get(pk=order.id).status,'CLOSED')
    def test_gate_and_ownership_reject_before_provider_calls(self):
        from payments.wechat_service import create_prepay
        from payments.service import PaymentError
        order=self._order('WECHAT')
        with override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':False,'OFFLINE':False}):
            with self.assertRaises(PaymentError):create_prepay(self.member,order.id,uuid.uuid4())
        with self.assertRaises(PaymentError):create_prepay(SimpleNamespace(id=uuid.uuid4()),order.id,uuid.uuid4())
        self.gateway.prepay.assert_not_called()
    def test_non_success_query_omits_optional_fields_and_remote_closed_releases(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import query_payment
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.query.side_effect=None
        self.gateway.query.return_value={'appid':a.app_id,'mchid':a.merchant_id,'out_trade_no':a.out_trade_no,'trade_state':'CLOSED'}
        result=query_payment(self.member,order.id)
        self.assertEqual(result['order']['status'],'CLOSED')
        self.assertEqual(result['paymentState'],'CLOSED')
        self.assertFalse(WechatPaymentAttempt.objects.get().close_requested)
    def test_signed_not_created_is_terminal_when_local_order_closed(self):
        from payments.wechat_gateway import WechatGatewayError
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import reconcile_attempt
        order=self._order('WECHAT');self.prepay(order);cancel_order(self.member,order.id)
        a=WechatPaymentAttempt.objects.get()
        self.gateway.query.side_effect=WechatGatewayError('支付单不存在。','WECHAT_ORDERNOTEXIST',404)
        reconcile_attempt(a.id)
        a.refresh_from_db();self.assertEqual(a.state,'CLOSED');self.assertFalse(a.close_requested)
        self.gateway.close.assert_not_called()
    def test_delayed_new_snapshot_reuses_ready_and_never_overwrites_success(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_intents import acquire
        from payments.service import PaymentError
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        with self.assertRaises(PaymentError):acquire(a.id,'PREPAY')
        WechatPaymentAttempt.objects.filter(pk=a.id).update(trade_state='SUCCESS',state='PAID')
        with self.assertRaises(PaymentError):acquire(a.id,'PREPAY')
        self.assertEqual(WechatPaymentAttempt.objects.get().state,'PAID')
    def test_member_api_bearer_ownership_empty_body_and_replay(self):
        order=self._order('WECHAT');token,_=MemberSession.issue(self.member)
        url=f'/api/v1/app/orders/{order.id}/wechat/prepay';key=str(uuid.uuid4())
        self.assertEqual(self.client.post(url,{},content_type='application/json').status_code,401)
        headers={'HTTP_AUTHORIZATION':f'Bearer {token}','HTTP_IDEMPOTENCY_KEY':key}
        self.assertEqual(self.client.post(url,{'amount':1},content_type='application/json',**headers).status_code,400)
        self.assertEqual(self.client.post(url,{},content_type='application/json',**headers).status_code,200)
        self.assertEqual(self.client.post(url,{},content_type='application/json',**headers).status_code,200)
        self.gateway.prepay.assert_called_once()
        response=self.client.post(f'/api/v1/app/orders/{uuid.uuid4()}/wechat/query',{},content_type='application/json',**headers)
        self.assertEqual(response.status_code,404)
        detail=self.client.get(f'/api/v1/app/orders/{order.id}',**headers).json()['data']
        self.assertFalse(detail['wechatPaymentAvailable']) # absent merchant material fails closed
    def test_callback_protocol_invalid_signature_unconfigured_and_body_limit(self):
        from payments.wechat_gateway import WechatGatewayError
        url='/api/v1/payments/wechat/notify'
        self.assertEqual(self.client.get(url).status_code,405)
        self.assertEqual(self.client.post(url,b'x'*65537,content_type='application/json').status_code,413)
        self.gateway.verify_notification.side_effect=WechatGatewayError('签名无效。','WECHAT_SIGNATURE_INVALID',401)
        self.assertEqual(self.client.post(url,b'{}',content_type='application/json').status_code,401)
        with patch('payments.wechat_service.get_gateway',side_effect=WechatGatewayError('未配置。','WECHAT_NOT_CONFIGURED',503)):
            self.assertEqual(self.client.post(url,b'{}',content_type='application/json').status_code,503)
        self.assertFalse(PaymentReceipt.objects.exists())
    def test_callback_returns_204_and_accepts_after_public_gate_closed(self):
        from payments.models import WechatPaymentAttempt
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.verify_notification.return_value=('WX-EVENT',self.success(a))
        with override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT':False,'OFFLINE':False}):
            self.assertEqual(self.client.post('/api/v1/payments/wechat/notify',b'{}',content_type='application/json').status_code,204)
        self.assertEqual(Order.objects.get(pk=order.id).status,'PAID')
    def test_unknown_order_verified_funds_are_anomaly_and_not_discarded(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import handle_notification
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.verify_notification.return_value=('WX-UNKNOWN',self.success(a,out_trade_no='EXT|ORDER*'+uuid.uuid4().hex[:20]))
        self.assertEqual(handle_notification({},b'raw')['outcome'],'ANOMALY')
        self.assertEqual(PaymentAnomaly.objects.get().reason,'UNKNOWN_ORDER')
    def test_old_query_cannot_downgrade_new_notification(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import handle_notification,query_payment
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.verify_notification.return_value=('WX-EVENT',self.success(a))
        def old_response(out_trade_no):
            handle_notification({},b'raw')
            return self.notpay(out_trade_no)
        self.gateway.query.side_effect=old_response
        self.assertEqual(query_payment(self.member,order.id)['paymentState'],'SUCCESS')
        a.refresh_from_db();self.assertEqual(a.state,'PAID')
    def test_payment_identity_alias_and_completed_operation_are_db_immutable(self):
        from payments.models import WechatPaymentAttempt,WechatPrepayRequest,WechatOperation
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        for call in [lambda:WechatPaymentAttempt.objects.filter(pk=a.id).update(amount_fen=1),
                     lambda:WechatPaymentAttempt.objects.filter(pk=a.id).delete(),
                     lambda:WechatPrepayRequest.objects.update(key=uuid.uuid4()),
                     lambda:WechatOperation.objects.update(error_code='REWRITTEN')]:
            with self.assertRaises(DatabaseError),transaction.atomic():call()
    def test_retry_key_is_bound_to_order_and_aliases_are_bounded(self):
        from payments.wechat_service import create_prepay
        from payments.service import PaymentError
        order=self._order('WECHAT');key=uuid.uuid4();self.prepay(order,key)
        other=self._order('WECHAT')
        with self.assertRaises(PaymentError):create_prepay(self.member,other.id,key)
        for _ in range(19):self.prepay(order)
        with self.assertRaises(PaymentError) as exc:self.prepay(order)
        self.assertEqual(exc.exception.status,429)
    def test_stale_lease_cannot_overwrite_new_operation(self):
        from payments.models import WechatPaymentAttempt,WechatOperation
        from payments.wechat_intents import acquire,finish
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        _,old_token,old_op=acquire(a.id,'QUERY')
        WechatPaymentAttempt.objects.filter(pk=a.id).update(lease_until=timezone.now()-timedelta(seconds=1))
        _,new_token,new_op=acquire(a.id,'QUERY')
        self.assertFalse(finish(a.id,old_token,old_op,trade_state='CLOSED',state='CLOSED'))
        self.assertTrue(finish(a.id,new_token,new_op,trade_state='NOTPAY',state='READY'))
        self.assertEqual(WechatOperation.objects.get(pk=old_op).result,'SUPERSEDED')
        self.assertEqual(WechatPaymentAttempt.objects.get().trade_state,'NOTPAY')
    def test_close_payment_race_defers_then_records_late_funds(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_gateway import WechatGatewayError
        from payments.wechat_service import reconcile_attempt
        from payments.service import PaymentError
        order=self._order('WECHAT');self.prepay(order);cancel_order(self.member,order.id);a=WechatPaymentAttempt.objects.get()
        self.gateway.close.side_effect=WechatGatewayError('订单已支付。','WECHAT_ORDERPAID',409)
        with self.assertRaises(PaymentError):reconcile_attempt(a.id)
        self.assertEqual(WechatPaymentAttempt.objects.get().state,'CLOSE_UNKNOWN')
        self.gateway.query.side_effect=None;self.gateway.query.return_value=self.success(a)
        reconcile_attempt(a.id)
        self.assertEqual(Order.objects.get(pk=order.id).status,'CLOSED')
        self.assertEqual(PaymentAnomaly.objects.get().reason,'CLOSED_ORDER')
    def test_operation_rate_limit_and_unconfigured_scheduler(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from payments.models import WechatPaymentAttempt
        from payments.wechat_intents import acquire,finish
        from payments.service import PaymentError
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        for _ in range(29):
            _,token,op=acquire(a.id,'QUERY');finish(a.id,token,op)
        with self.assertRaises(PaymentError) as exc:acquire(a.id,'QUERY')
        self.assertEqual(exc.exception.status,429)
        with self.assertRaises(CommandError):call_command('reconcile_wechat_payments')
    def test_parallel_prepay_holds_no_stock_lock_and_calls_provider_once(self):
        import threading
        from django.db import close_old_connections
        from payments.wechat_service import create_prepay
        from payments.service import PaymentError
        order=self._order('WECHAT');key=uuid.uuid4();entered=threading.Event();release=threading.Event();results=[]
        original=self.gateway.prepay.return_value
        def network(*args):
            self.assertFalse(connection.in_atomic_block);entered.set()
            if not release.wait(5):raise RuntimeError('test timeout')
            return original
        self.gateway.prepay.side_effect=network
        def first():
            close_old_connections()
            try:results.append(create_prepay(self.member,order.id,key))
            except Exception as exc:results.append(exc)
            finally:connection.close()
        worker=threading.Thread(target=first);worker.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(PaymentError) as exc:create_prepay(self.member,order.id,key)
            self.assertEqual(exc.exception.code,'WECHAT_BUSY')
        finally:release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertIsInstance(results[0],dict)
        self.assertEqual(self.gateway.prepay.call_count,1)
    def test_notification_cancel_race_has_one_consistent_stock_result(self):
        import threading
        from django.db import close_old_connections
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import handle_notification
        from orders.service import OrderError
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.verify_notification.return_value=('WX-RACE',self.success(a))
        gate=threading.Barrier(2);results=[]
        def worker(index):
            close_old_connections()
            try:
                gate.wait(5)
                results.append(handle_notification({},b'raw') if index==0 else cancel_order(self.member,order.id))
            except OrderError as exc:results.append(exc)
            except Exception as exc:results.append(exc)
            finally:connection.close()
        workers=[threading.Thread(target=worker,args=(i,)) for i in range(2)]
        for item in workers:item.start()
        for item in workers:item.join(8)
        self.assertTrue(all(not item.is_alive() for item in workers));self.assertEqual(len(results),2)
        self.assertFalse(any(isinstance(result,Exception) and not isinstance(result,OrderError) for result in results))
        order.refresh_from_db();self.balance.refresh_from_db()
        self.assertIn(order.status,['PAID','CLOSED']);self.assertEqual(PaymentReceipt.objects.count(),1)
        self.assertEqual(self.balance.on_hand_base_units,8 if order.status=='PAID' else 10)
    def test_non_success_optional_objects_allow_partial_fields(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_service import query_payment
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        self.gateway.query.side_effect=None
        self.gateway.query.return_value={'appid':a.app_id,'mchid':a.merchant_id,'out_trade_no':a.out_trade_no,
                                         'trade_state':'NOTPAY','amount':{'total':2000},'payer':{}}
        self.assertEqual(query_payment(self.member,order.id)['paymentState'],'NOTPAY')
    def test_cancel_during_prepay_discards_parameters_and_keeps_close_queue(self):
        from payments.models import WechatPaymentAttempt
        from payments.service import PaymentError
        order=self._order('WECHAT');response=self.gateway.prepay.return_value
        def in_flight(*args):
            cancel_order(self.member,order.id)
            return response
        self.gateway.prepay.side_effect=in_flight
        with self.assertRaises(PaymentError) as exc:self.prepay(order)
        self.assertEqual(exc.exception.code,'ORDER_NOT_PAYABLE')
        self.assertTrue(WechatPaymentAttempt.objects.get().close_requested)
        self.assertFalse(PaymentReceipt.objects.exists())
    def test_old_new_snapshot_must_query_after_other_request_failed(self):
        from payments.models import WechatPaymentAttempt
        from payments.wechat_intents import acquire
        from payments.service import PaymentError
        order=self._order('WECHAT');self.prepay(order);a=WechatPaymentAttempt.objects.get()
        WechatPaymentAttempt.objects.filter(pk=a.id).update(state='UNKNOWN')
        with self.assertRaises(PaymentError) as exc:acquire(a.id,'PREPAY')
        self.assertEqual(exc.exception.code,'WECHAT_QUERY_REQUIRED')

    @override_settings(ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': False, 'OFFLINE': False})
    def test_admin_wechat_flag_controls_prepay_and_unconfigured_gateway_has_no_funds(self):
        from payments.models import OfflinePaymentPolicy, WechatPaymentAttempt
        from payments.wechat_gateway import WechatGatewayError
        from payments.service import PaymentError
        OfflinePaymentPolicy.objects.filter(pk=1).update(wechat_enabled=True)
        order = self._order('WECHAT')
        with patch('payments.wechat_service.get_gateway', side_effect=WechatGatewayError('未配置。', 'WECHAT_NOT_CONFIGURED', 503)):
            with self.assertRaises(PaymentError) as caught:
                self.prepay(order)
        self.assertEqual(caught.exception.code, 'WECHAT_NOT_CONFIGURED')
        self.assertFalse(WechatPaymentAttempt.objects.exists())
        self.assertFalse(PaymentReceipt.objects.exists())
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'PENDING_PAYMENT')
        self.prepay(order)
        self.assertEqual(WechatPaymentAttempt.objects.count(), 1)
        OfflinePaymentPolicy.objects.filter(pk=1).update(wechat_enabled=False)
        with self.assertRaises(PaymentError) as caught:
            self.prepay(order)
        self.assertEqual(caught.exception.code, 'PAYMENT_METHOD_DISABLED')
