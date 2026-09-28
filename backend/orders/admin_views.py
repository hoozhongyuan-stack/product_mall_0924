from accounts.security import error, require, response
from accounts.views import method
from payments.service import PaymentError
from .models import Order
from .queries import admin_order_data, list_orders


def orders_view(request):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, denied = require(request, 'order.read')
    if denied:
        return denied
    try:
        return response(request, list_orders(request.GET))
    except PaymentError as exc:
        return error(request, exc.status, exc.code, str(exc))


def order_detail_view(request, order_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, denied = require(request, 'order.read')
    if denied:
        return denied
    order = Order.objects.filter(pk=order_id).first()
    return response(request, admin_order_data(order)) if order else error(request, 404, 'ORDER_NOT_FOUND', '订单不存在。')
