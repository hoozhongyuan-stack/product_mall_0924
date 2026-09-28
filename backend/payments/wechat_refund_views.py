"""Authorized admin dispatch/query and provider-only cryptographic callback."""
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from accounts.models import AdminAccount
from accounts.security import require, parse_json, permissions, confirm_action, audit
from accounts.views import method
from aftersales.models import AfterSaleCase
from aftersales.views import result, fail, key
from orders.models import Order
from .access import applied_receipt
from .models import RefundIntent, RefundOperation
from .refunds import RefundError, prepare_refund
from .wechat_config import WechatGatewayError
from .wechat_refund_service import dispatch_intent, query_intent, _enabled_gateway


def _authorized(request):
    actor, bad = require(request, "aftersale.read")
    if bad:
        return actor, bad
    return require(request, "refund.prepare")


def _live(actor):
    row = AdminAccount.objects.select_for_update().get(pk=actor.pk)
    if not row.enabled or not {"aftersale.read", "refund.prepare"}.issubset(permissions(row)):
        raise RefundError("当前账号没有微信退款操作权限。", "PERMISSION_DENIED", 403)
    return row


def _limited(case_id):
    if RefundOperation.objects.filter(intent__case_id=case_id,
            started_at__gte=timezone.now() - timezone.timedelta(minutes=15)).count() >= 30:
        raise RefundError("退款核查操作过多，请稍后再试。", "RATE_LIMITED", 429)


def dispatch_view(request, case_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = _authorized(request)
    if bad:
        return bad
    try:
        body = parse_json(request)
        if set(body) != {"expectedRevision"} or type(body["expectedRevision"]) is not int or body["expectedRevision"] < 1:
            raise RefundError("退款发起须提供售后修订号，不提交资金字段。")
        request_key = key(request)
        _limited(case_id)
        _enabled_gateway()  # Missing configuration never creates a dispatch operation.
        with transaction.atomic():
            oid = AfterSaleCase.objects.filter(pk=case_id).values_list("order_line__order_id", flat=True).first()
            if oid is None:
                raise RefundError("售后申请不存在。", "AFTERSALE_NOT_FOUND", 404)
            order = Order.objects.select_for_update().get(pk=oid)
            case = AfterSaleCase.objects.select_for_update().get(pk=case_id)
            live = _live(actor)
            if case.revision != body["expectedRevision"]:
                raise RefundError("售后已更新，请刷新后再确认。", "REVISION_CONFLICT", 409)
            receipt = applied_receipt(order)
            if receipt is None or receipt.channel != "WECHAT":
                raise RefundError("该订单不使用微信资金退款。", "WECHAT_REFUND_NOT_READY", 409)
            if case.status != "WAITING_REFUND":
                raise RefundError("售后尚不满足退款条件。", "REFUND_NOT_READY", 409)
            denied = confirm_action(request, live, "refund.wechat.dispatch", case.id, case.revision)
        if denied:
            return denied
        intent = prepare_refund(case_id, live, request_key)
        data = dispatch_intent(intent.id, actor.id)
        audit(request, "refund.wechat.dispatch", "aftersale", case_id, actor)
        return result(request, data)
    except ValueError as exc:
        return fail(request, exc)


def query_view(request, case_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = _authorized(request)
    if bad:
        return bad
    try:
        if parse_json(request):
            raise RefundError("退款查单不接受资金字段。")
        _limited(case_id)
        with transaction.atomic():
            _live(actor)
        intent = RefundIntent.objects.filter(case_id=case_id, channel="WECHAT").first()
        if not intent:
            raise RefundError("微信退款单不存在。", "WECHAT_REFUND_NOT_FOUND", 404)
        data = query_intent(intent.id, actor.id)
        audit(request, "refund.wechat.query", "aftersale", case_id, actor)
        return result(request, data)
    except ValueError as exc:
        return fail(request, exc)


@csrf_exempt
def notification_view(request):
    if request.method != "POST":
        return HttpResponse(status=405, headers={"Allow": "POST"})
    from .wechat_refund_service import handle_refund_notification
    try:
        raw = request.read(65537)
        if len(raw) > 65536:
            return JsonResponse({"code": "FAIL", "message": "通知报文过大"}, status=413)
        handle_refund_notification(request.headers, raw)
        return HttpResponse(status=204)
    except (RefundError, WechatGatewayError) as exc:
        return JsonResponse({"code": "FAIL", "message": str(exc)}, status=exc.status)
