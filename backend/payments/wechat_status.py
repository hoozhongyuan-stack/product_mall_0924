"""Read-only payment availability; never creates a provider connection."""
from .availability import payment_method_enabled
from django.utils import timezone
from .models import WechatPaymentAttempt
from .wechat_gateway import WechatGateway, WechatGatewayError


def payment_status(order):
    if order.payment_method != 'WECHAT':
        return {'wechatPaymentAvailable':False,'wechatPaymentState':None}
    attempt = WechatPaymentAttempt.objects.filter(order_id=order.id).only('trade_state').first()
    state = attempt.trade_state if attempt else 'NOTPAY'
    available = bool(payment_method_enabled('WECHAT')
                     and order.status=='PENDING_PAYMENT' and order.payable_fen>0
                     and order.expires_at>timezone.now() and state not in {'SUCCESS','USERPAYING','CLOSED','REVOKED','PAYERROR'})
    if available:
        try:
            WechatGateway()  # Loads and parses key material, with no network request.
        except WechatGatewayError:
            available = False
    return {'wechatPaymentAvailable':available,'wechatPaymentState':state}
