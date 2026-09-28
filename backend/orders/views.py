"""Member-only order endpoints; public submission awaits collection confirmation."""

from uuid import UUID

from django.views.decorators.csrf import csrf_exempt

from accounts.security import error, parse_json, response
from accounts.views import method
from customers.auth import resolve_member

from .models import Order
from .service import OrderError, cancel_order, order_data, submit_order


def _member(request):
    member = resolve_member(request)
    if member is None:
        return None, error(request, 401, "LOGIN_REQUIRED", "请先完成微信登录。")
    return member, None


@csrf_exempt
def orders_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    member, denied = _member(request)
    if denied:
        return denied
    if request.method == "GET":
        from .queries import list_orders
        from payments.service import PaymentError
        try:
            return response(request, list_orders(request.GET, member))
        except PaymentError as exc:
            return error(request, exc.status, exc.code, str(exc))
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
    except (ValueError, AttributeError):
        return error(request, 400, "VALIDATION_FAILED", "请提供 UUID 格式的防重复请求标识。")
    try:
        data, replayed = submit_order(member, parse_json(request), key)
        return response(request, data, status=200 if replayed else 201)
    except OrderError as exc:
        return error(request, exc.status, exc.code, str(exc), exc.details)
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))


@csrf_exempt
def order_detail_view(request, order_id):
    bad = method(request, "GET")
    if bad:
        return bad
    member, denied = _member(request)
    if denied:
        return denied
    order = Order.objects.filter(pk=order_id, member_id=member.id).first()
    if order is None:
        return error(request, 404, "ORDER_NOT_FOUND", "订单不存在。")
    result = response(request, order_data(order, include_voucher_code=True))
    result["Cache-Control"] = "private, no-store"
    return result


@csrf_exempt
def order_cancel_view(request, order_id):
    bad = method(request, "POST")
    if bad:
        return bad
    member, denied = _member(request)
    if denied:
        return denied
    try:
        if parse_json(request):
            raise OrderError("取消订单无需附加字段。")
        return response(request, cancel_order(member, order_id))
    except OrderError as exc:
        return error(request, exc.status, exc.code, str(exc), exc.details)
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
