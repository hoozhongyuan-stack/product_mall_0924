"""Member bearer APIs and the provider-only, cryptographically verified callback."""
from uuid import UUID
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from accounts.security import error, parse_json, response
from accounts.views import method
from customers.auth import resolve_member
from .service import PaymentError
from .wechat_service import create_prepay, handle_notification, query_payment


def _member_action(request, order_id, prepay):
    bad = method(request,'POST')
    if bad:
        return bad
    member = resolve_member(request)
    if not member:
        return error(request,401,'LOGIN_REQUIRED','请先完成微信登录。')
    try:
        if parse_json(request):
            raise PaymentError('支付接口无需附加交易字段。')
        if prepay:
            try:
                key = UUID(request.headers.get('Idempotency-Key',''))
            except (ValueError, TypeError, AttributeError):
                raise PaymentError('请提供 UUID 格式的防重复请求标识。') from None
            data = create_prepay(member,order_id,key)
        else:
            data = query_payment(member,order_id)
        return response(request,data)
    except (PaymentError, ValueError) as exc:
        return error(request,getattr(exc,'status',400),getattr(exc,'code','VALIDATION_FAILED'),str(exc))


@csrf_exempt
def wechat_prepay_view(request, order_id):
    return _member_action(request,order_id,True)


@csrf_exempt
def wechat_query_view(request, order_id):
    return _member_action(request,order_id,False)


@csrf_exempt
def wechat_notification_view(request):
    if request.method != 'POST':
        return HttpResponse(status=405, headers={'Allow':'POST'})
    try:
        # Callback body is bounded before cryptographic parsing; APIv3 max here64KiB.
        raw_body = request.read(65537)
        if len(raw_body) > 65536:
            return JsonResponse({'code':'FAIL','message':'通知报文过大'},status=413)
        handle_notification(request.headers,raw_body)
        return HttpResponse(status=204)
    except PaymentError as exc:
        return JsonResponse({'code':'FAIL','message':str(exc)},status=exc.status)
