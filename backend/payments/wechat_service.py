"""WeChat orchestration: short locks, external I/O, durable funds settlement."""
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from orders.models import Order
from orders.service import close_expired_order_locked, close_wechat_order, order_data
from .models import WechatPaymentAttempt
from .service import PaymentError
from .wechat_gateway import WechatGateway, WechatGatewayError
from .wechat_intents import CachedPrepay, QueryRequired, acquire, bind_request, finish, outside_transaction
from .wechat_verification import accept_success, validate_payload


def get_gateway():
    return WechatGateway()


def _owned_order(member, order_id, *, locked=False):
    query = Order.objects.select_for_update() if locked else Order.objects
    order = query.filter(pk=order_id, member_id=member.id).first()
    if not order:
        raise PaymentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
    if order.payment_method != 'WECHAT' or order.payable_fen <= 0:
        raise PaymentError('该订单不使用微信支付。', 'WECHAT_ORDER_INVALID', 409)
    return order


def _gateway_error(exc):
    return PaymentError(str(exc), exc.code, exc.status)


def _call(attempt_id, kind, call, *, on_result=None):
    outside_transaction()
    attempt, token, operation_id = acquire(attempt_id, kind)
    try:
        data = call(attempt)
        changes = on_result(data, attempt) if on_result else {}
        if not finish(attempt_id, token, operation_id, **changes):
            raise PaymentError('支付操作结果已被新的核查取代，请刷新订单。', 'WECHAT_OPERATION_SUPERSEDED', 409)
        return data
    except (WechatGatewayError, PaymentError) as exc:
        finish(attempt_id, token, operation_id, error_code=exc.code,
               state='CLOSE_UNKNOWN' if kind == 'CLOSE' else 'UNKNOWN')
        raise _gateway_error(exc) from None
    except Exception:
        finish(attempt_id, token, operation_id, error_code='WECHAT_INTERNAL_ERROR', state='UNKNOWN')
        raise PaymentError('支付操作未完成，请查询订单状态。', 'WECHAT_INTERNAL_ERROR', 503) from None


def _query(attempt, gateway):
    def external(row):
        try:
            return gateway.query(row.out_trade_no)
        except WechatGatewayError as exc:
            if exc.code == 'WECHAT_ORDERNOTEXIST':
                return None  # Only a signed provider error proves it was not created.
            raise
    def apply(payload, row):
        if payload is None:
            if row.trade_state == 'SUCCESS':
                raise PaymentError('微信支付状态发生冲突，请核查。', 'WECHAT_STATE_CONFLICT', 409)
            if row.close_requested:
                return {'trade_state':'CLOSED', 'state':'CLOSED', 'close_requested':False}
            return {'trade_state':'NOTPAY', 'state':'NEW'}
        state = validate_payload(payload, gateway.config, row)
        if state == 'SUCCESS':
            accept_success(payload, gateway.config)
            return {}
        if row.trade_state == 'SUCCESS':
            raise PaymentError('微信支付状态发生冲突，请核查。', 'WECHAT_STATE_CONFLICT', 409)
        if state in {'CLOSED','REVOKED'}:
            close_wechat_order(row.order_id)
        return {'trade_state':state, 'state':'CLOSED' if state in {'CLOSED','REVOKED'} else 'READY' if row.prepay_id else 'NEW' if state=='NOTPAY' else 'UNKNOWN',
                **({'close_requested':False} if state in {'CLOSED','REVOKED'} else {})}
    return _call(attempt.id, 'QUERY', external, on_result=apply)


def _prepare_attempt(member, order_id, key, config):
    with transaction.atomic():
        order = _owned_order(member, order_id, locked=True)
        close_expired_order_locked(order)
        eligible = order.status == 'PENDING_PAYMENT' and order.expires_at > timezone.now()
        if eligible and (member.wechat_app_id != config.app_id or not member.wechat_openid):
            raise PaymentError('微信登录身份与支付应用不一致。', 'WECHAT_IDENTITY_CHANGED', 409)
        attempt = bind_request(member, order, key, config) if eligible else None
    if not eligible:
        raise PaymentError('订单已关闭或已付款，请刷新。', 'ORDER_NOT_PAYABLE', 409)
    return attempt


def _prepay_result(attempt, gateway):
    def external(row):
        current = Order.objects.get(pk=row.order_id)
        if current.status != 'PENDING_PAYMENT' or current.expires_at <= timezone.now():
            raise PaymentError('订单已关闭，请刷新。', 'ORDER_NOT_PAYABLE', 409)
        return gateway.prepay(row.out_trade_no, f'商城订单 {current.order_no}',
                              row.amount_fen, row.payer_openid, row.expires_at)
    def apply(data, row):
        return {'state':'READY','trade_state':'NOTPAY','prepay_id':data['prepayId'],
                'prepay_expires_at':min(row.expires_at, timezone.now()+timedelta(hours=2))}
    for retry in range(2):
        try:
            data = _call(attempt.id, 'PREPAY', external, on_result=apply)
            break
        except CachedPrepay as cached:
            data = {'paymentParameters':gateway.payment_parameters(cached.attempt.prepay_id)}
            break
        except QueryRequired as required:
            if retry:
                raise
            _query(required.attempt, gateway)
    current = Order.objects.get(pk=attempt.order_id)
    attempt.refresh_from_db()
    if (current.status != 'PENDING_PAYMENT' or current.expires_at <= timezone.now() or
            attempt.trade_state in {'SUCCESS','CLOSED','REVOKED','USERPAYING','PAYERROR'}):
        raise PaymentError('订单状态已变化，请刷新。', 'ORDER_NOT_PAYABLE', 409)
    return {'attemptId':str(attempt.id),'paymentParameters':data['paymentParameters'],
            'expiresAt':attempt.prepay_expires_at.isoformat()}


def create_prepay(member, order_id, key):
    outside_transaction()
    if not isinstance(key, uuid.UUID):
        raise PaymentError('请提供 UUID 格式的防重复请求标识。')
    # Ownership and the public gate precede config loading/provider access.
    _owned_order(member, order_id)
    if not getattr(settings,'ORDER_PAYMENT_METHODS_ENABLED',{}).get('WECHAT',False):
        raise PaymentError('微信支付暂未开放。', 'PAYMENT_METHOD_DISABLED', 409)
    try:
        gateway = get_gateway()
        attempt = _prepare_attempt(member, order_id, key, gateway.config)
        if attempt.trade_state in {'SUCCESS','USERPAYING','CLOSED','REVOKED','PAYERROR'}:
            raise PaymentError('微信交易已到账、结束或处理中，请刷新。', 'WECHAT_NOT_PAYABLE', 409)
        if (attempt.state not in {'NEW','READY'} or
                (attempt.prepay_expires_at and attempt.prepay_expires_at <= timezone.now())):
            _query(attempt, gateway)
            attempt.refresh_from_db()
            if attempt.trade_state != 'NOTPAY':
                raise PaymentError('微信交易正在处理或已结束，请刷新。', 'WECHAT_NOT_PAYABLE', 409)
        return _prepay_result(attempt, gateway)
    except WechatGatewayError as exc:
        raise _gateway_error(exc) from None


def query_payment(member, order_id):
    outside_transaction()
    order = _owned_order(member, order_id)
    attempt = WechatPaymentAttempt.objects.filter(order=order).first()
    if not attempt:
        return {'paymentState':'NOTPAY' if order.status=='PENDING_PAYMENT' else 'CLOSED','order':order_data(order, include_voucher_code=True)}
    try:
        _query(attempt, get_gateway())
    except WechatGatewayError as exc:
        raise _gateway_error(exc) from None
    order.refresh_from_db(); attempt.refresh_from_db()
    return {'paymentState':attempt.trade_state,'order':order_data(order, include_voucher_code=True)}


def handle_notification(headers, raw_body):
    outside_transaction()
    try:
        gateway = get_gateway()
        event_id, payload = gateway.verify_notification(headers, raw_body)
        return accept_success(payload, gateway.config, event_id=event_id)
    except WechatGatewayError as exc:
        raise _gateway_error(exc) from None


def reconcile_attempt(attempt_id):
    outside_transaction()
    attempt = WechatPaymentAttempt.objects.select_related('order').get(pk=attempt_id)
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=attempt.order_id)
        close_expired_order_locked(order)
    try:
        gateway = get_gateway()
        _query(attempt, gateway)
        attempt.refresh_from_db()
        if attempt.close_requested and attempt.trade_state == 'NOTPAY':
            _call(attempt.id,'CLOSE',lambda row:gateway.close(row.out_trade_no),
                  on_result=lambda data,row:{'state':'CLOSED','trade_state':'CLOSED','close_requested':False})
        return attempt.id
    except WechatGatewayError as exc:
        raise _gateway_error(exc) from None
