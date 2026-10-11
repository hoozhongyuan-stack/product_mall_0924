"""Admin payment reconciliation reuses provider leases and verified settlement."""
from django.db import transaction
from accounts.security import operation_permissions
from orders.models import Order
from orders.queries import admin_order_data
from .service import PaymentError
from .wechat_gateway import WechatGatewayError
from .wechat_service import _query, _gateway_error, get_gateway
from .models import WechatPaymentAttempt
from .wechat_intents import outside_transaction


def query_admin_payment(order_id, actor):
    outside_transaction()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if 'payment.offline.confirm' not in operation_permissions(actor):
            raise PaymentError('当前账号没有此操作权限。', 'PERMISSION_DENIED', 403)
        if not order:
            raise PaymentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
        if order.payment_method != 'WECHAT' or order.payable_fen <= 0:
            raise PaymentError('该订单不使用微信支付。', 'WECHAT_ORDER_INVALID', 409)
        attempt = WechatPaymentAttempt.objects.filter(order=order).first()
    if attempt:
        try:
            _query(attempt, get_gateway())
        except WechatGatewayError as exc:
            raise _gateway_error(exc) from None
        order.refresh_from_db()
        attempt.refresh_from_db()
    return {'paymentState': attempt.trade_state if attempt else ('NOTPAY' if order.status == 'PENDING_PAYMENT' else 'CLOSED'),
            'order': admin_order_data(order)}
