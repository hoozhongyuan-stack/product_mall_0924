"""Admin HTTP wrapper for the payments domain's reconciliation entry point."""
from django.views.decorators.cache import cache_control
from accounts.security import audit, error, parse_json, require, response
from common.http import method
from payments.service import PaymentError
from payments.admin_query import query_admin_payment as query


@cache_control(private=True, no_store=True)
def query_view(request, order_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, denied = require(request, 'payment.offline.confirm')
    if denied:
        return denied
    try:
        if parse_json(request):
            raise PaymentError('查询支付结果无需附加交易字段。')
        data = query(order_id, actor)
        audit(request, 'payment.wechat.query', 'order', order_id, actor=actor, after={'paymentState': data['paymentState']})
        return response(request, data)
    except (PaymentError, ValueError) as exc:
        return error(request, getattr(exc, 'status', 400), getattr(exc, 'code', 'VALIDATION_FAILED'), str(exc))
