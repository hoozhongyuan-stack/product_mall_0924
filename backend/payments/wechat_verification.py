"""Translate verified provider facts to the shared, durable C0 funds entry."""
from datetime import datetime
from django.utils import timezone
from .models import WechatPaymentAttempt
from .service import PaymentError, VerifiedPayment, record_verified_payment


def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit and value == value.strip() and all(ord(c) >= 32 for c in value)


def validate_payload(payload, config, attempt=None):
    if not isinstance(payload, dict):
        raise PaymentError('微信支付交易资料不正确。', 'WECHAT_TRANSACTION_INVALID', 400)
    amount, payer = payload.get('amount'), payload.get('payer')
    state = payload.get('trade_state')
    if (payload.get('appid') != config.app_id or payload.get('mchid') != config.merchant_id or
        not _text(payload.get('out_trade_no'),32) or len(payload['out_trade_no']) < 6 or
        not isinstance(state,str) or state not in {'SUCCESS','NOTPAY','USERPAYING','CLOSED','REVOKED','PAYERROR'}):
        raise PaymentError('微信支付交易身份不正确。', 'WECHAT_TRANSACTION_INVALID', 400)
    # Before success, every optional object and each of its fields may be absent.
    if state == 'SUCCESS' and (not isinstance(amount,dict) or not {'total','currency'} <= amount.keys()):
        raise PaymentError('微信支付到账金额资料不完整。', 'WECHAT_TRANSACTION_INVALID', 400)
    if amount is not None and (not isinstance(amount,dict) or
            ('total' in amount and (type(amount['total']) is not int or not 0 < amount['total'] <= 9223372036854775807)) or
            ('currency' in amount and amount['currency'] != 'CNY')):
        raise PaymentError('微信支付金额资料不正确。', 'WECHAT_TRANSACTION_INVALID', 400)
    if state == 'SUCCESS' and (not isinstance(payer,dict) or 'openid' not in payer):
        raise PaymentError('微信支付付款人资料不完整。', 'WECHAT_TRANSACTION_INVALID', 400)
    if payer is not None and (not isinstance(payer,dict) or ('openid' in payer and not _text(payer['openid'],128))):
        raise PaymentError('微信支付付款人资料不正确。', 'WECHAT_TRANSACTION_INVALID', 400)
    if (state == 'SUCCESS' or 'trade_type' in payload) and payload.get('trade_type') != 'JSAPI':
        raise PaymentError('微信支付交易类型不正确。', 'WECHAT_TRANSACTION_INVALID', 400)
    if attempt and (payload['out_trade_no'] != attempt.out_trade_no or
                    (payload['appid'], payload['mchid']) != (attempt.app_id,attempt.merchant_id) or
                    (payer is not None and 'openid' in payer and payer['openid'] != attempt.payer_openid)):
        raise PaymentError('微信支付交易与原支付单不一致。', 'WECHAT_TRANSACTION_INVALID', 400)
    return state


def accept_success(payload, config, *, event_id=''):
    attempt = WechatPaymentAttempt.objects.select_related('order').filter(out_trade_no=payload.get('out_trade_no')).first()
    if validate_payload(payload, config, attempt) != 'SUCCESS' or not _text(payload.get('transaction_id'), 128):
        raise PaymentError('通知不是有效的微信到账交易。', 'WECHAT_TRANSACTION_INVALID', 400)
    try:
        paid_at = datetime.fromisoformat(payload['success_time'].replace('Z','+00:00'))
        if not timezone.is_aware(paid_at) or paid_at > timezone.now() + timezone.timedelta(minutes=5):
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise PaymentError('微信到账时间不正确。', 'WECHAT_TRANSACTION_INVALID', 400) from None
    # Persist the signed success snapshot before settlement; no retry may repay.
    if attempt:
        WechatPaymentAttempt.objects.filter(pk=attempt.id).update(trade_state='SUCCESS', next_check_at=timezone.now())
    result = record_verified_payment(VerifiedPayment(
        order_no=attempt.order.order_no if attempt else 'WX'+payload['out_trade_no'], channel='WECHAT',
        merchant_account_id=config.merchant_id, external_trade_no=payload['transaction_id'],
        amount_fen=payload['amount']['total'], paid_at=paid_at,
        source='WECHAT_NOTIFICATION' if event_id else 'WECHAT_QUERY', event_id=event_id))
    if attempt:
        attempt.order.refresh_from_db(fields=['status'])
        WechatPaymentAttempt.objects.filter(pk=attempt.id).update(state='PAID' if attempt.order.status=='PAID' else 'ANOMALY')
    return result
