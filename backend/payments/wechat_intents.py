"""Durable provider intents; order locks always precede attempt locks."""
import uuid
from datetime import timedelta
from django.db import connection, transaction
from django.utils import timezone
from .models import WechatPaymentAttempt, WechatOperation, WechatPrepayRequest
from .service import PaymentError


class CachedPrepay(PaymentError):
    def __init__(self, attempt):
        super().__init__('已有可用预支付结果。', 'WECHAT_PREPAY_READY', 409)
        self.attempt = attempt


class QueryRequired(PaymentError):
    def __init__(self, attempt):
        super().__init__('支付结果未知，须先查单。', 'WECHAT_QUERY_REQUIRED', 409)
        self.attempt = attempt


def outside_transaction():
    if connection.in_atomic_block:
        raise PaymentError('支付网络操作不能放在业务事务中。', 'PAYMENT_TRANSACTION_NESTED', 500)


def mark_close_requested(order_id):
    """Called inside the local closing transaction; never calls the provider."""
    WechatPaymentAttempt.objects.filter(order_id=order_id).update(close_requested=True, next_check_at=timezone.now())


def bind_request(member, order, key, config):
    # Serialize aliases across different orders as well as requests on one order.
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', [f'wechat:{member.id}:{key}'])
    existing = WechatPrepayRequest.objects.filter(member_id=member.id, key=key).select_related('attempt').first()
    if existing and existing.attempt.order_id != order.id:
        raise PaymentError('防重复标识已用于另一订单。', 'IDEMPOTENCY_CONFLICT', 409)
    attempt, _ = WechatPaymentAttempt.objects.get_or_create(order=order, defaults={
        'out_trade_no': uuid.uuid4().hex, 'app_id': config.app_id, 'merchant_id': config.merchant_id,
        'payer_openid': member.wechat_openid, 'amount_fen': order.payable_fen, 'expires_at': order.expires_at})
    if (attempt.app_id, attempt.merchant_id, attempt.payer_openid) != (config.app_id, config.merchant_id, member.wechat_openid):
        raise PaymentError('支付身份配置与原支付单不一致，请核查。', 'WECHAT_IDENTITY_CHANGED', 409)
    if not existing:
        if WechatPrepayRequest.objects.filter(attempt=attempt).count() >= 20:
            raise PaymentError('请使用原支付请求标识重试。', 'WECHAT_RETRY_LIMIT', 429)
        WechatPrepayRequest.objects.create(member_id=member.id, key=key, attempt=attempt)
    return attempt


def acquire(attempt_id, kind):
    with transaction.atomic():
        row = WechatPaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        now = timezone.now()
        if row.lease_until and row.lease_until > now:
            raise PaymentError('支付状态正在核查，请稍后刷新。', 'WECHAT_BUSY', 409)
        WechatOperation.objects.filter(attempt=row, result='STARTED').update(result='SUPERSEDED', completed_at=now)
        if WechatOperation.objects.filter(attempt=row, started_at__gte=now-timedelta(minutes=1)).count() >= 30:
            raise PaymentError('支付核查过于频繁，请稍后重试。', 'WECHAT_RATE_LIMITED', 429)
        token = uuid.uuid4()
        row.lease_token, row.lease_until = token, now + timedelta(seconds=30)
        if kind == 'PREPAY':
            if row.trade_state in {'SUCCESS','CLOSED','REVOKED','USERPAYING','PAYERROR'} or row.close_requested or row.expires_at <= now:
                raise PaymentError('支付单已到账、关闭或处理中，请刷新。', 'WECHAT_NOT_PAYABLE', 409)
            if row.state not in {'NEW','READY'}:
                raise QueryRequired(row)
            if row.prepay_id and row.prepay_expires_at and row.prepay_expires_at > now:
                raise CachedPrepay(row)
            row.state = 'PREPAYING'
        row.save(update_fields=['lease_token','lease_until','state','updated_at'])
        operation = WechatOperation.objects.create(attempt=row, kind=kind)
        return row, token, operation.id


def finish(attempt_id, token, operation_id, *, error_code='', **changes):
    with transaction.atomic():
        row = WechatPaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        now = timezone.now()
        if row.lease_token != token:
            return False
        # A notification can win while a stale NOTPAY/prepay response is in flight.
        if row.trade_state == 'SUCCESS':
            changes = {k:v for k,v in changes.items() if k not in {'state','trade_state'}}
        for key, value in changes.items():
            setattr(row, key, value)
        row.lease_token = row.lease_until = None
        row.last_error_code = error_code
        row.next_check_at = now + timedelta(seconds=60)
        row.save()
        WechatOperation.objects.filter(pk=operation_id, result='STARTED').update(
            result='FAILED' if error_code else 'SUCCEEDED', error_code=error_code, completed_at=now)
        return True
