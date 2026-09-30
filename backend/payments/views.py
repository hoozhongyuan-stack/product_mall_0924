"""Offline configuration, member statements, and PC funds reconciliation."""
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from common.http import method
from accounts.security import audit, error, parse_json, require, require_live, response
from customers.auth import resolve_member
from orders.models import Order
from orders.queries import admin_order_data
from .models import OfflinePaymentPolicy, OfflineReconciliation
from .offline import clean_text, confirm_reconciliation, prepare_reconciliation, report_payment, request_key
from .policy import policy_data
from .service import PaymentError


def _failure(request, exc):
    return error(request, getattr(exc, 'status', 400), getattr(exc, 'code', 'VALIDATION_FAILED'), str(exc))


def offline_policy_view(request):
    bad = method(request, 'GET', 'PUT')
    if bad:
        return bad
    actor, denied = require(request, 'payment.settings.manage')
    if denied:
        return denied
    if request.method == 'GET':
        policy = OfflinePaymentPolicy.objects.filter(pk=1).first()
        return response(request, policy_data(policy)) if policy else error(request, 503, 'PAYMENT_POLICY_UNAVAILABLE', '付款配置不可用。')
    try:
        body = parse_json(request)
        required = {'instructions', 'merchantAccountId', 'wechatTimeoutMinutes', 'offlineTimeoutMinutes', 'expectedRevision'}
        modes = {'offlineEnabled', 'wechatEnabled'}
        if set(body) not in (required, required | modes):
            raise PaymentError('付款配置字段不正确。')
        if (not isinstance(body['instructions'], str) or len(body['instructions']) > 4000 or
                any(ord(c) < 32 and c not in '\n\r\t' for c in body['instructions']) or
                not isinstance(body['merchantAccountId'], str) or len(body['merchantAccountId']) > 80 or
                (body['merchantAccountId'] and not clean_text(body['merchantAccountId'], 80)) or
                type(body['expectedRevision']) is not int or body['expectedRevision'] < 1 or
                any(type(body[k]) is not int or not 1 <= body[k] <= 10080 for k in ('wechatTimeoutMinutes', 'offlineTimeoutMinutes'))):
            raise PaymentError('付款说明不超过 4000 字，待付款时限为 1 至 10080 分钟。')
        if any(type(body[key]) is not bool for key in modes if key in body):
            raise PaymentError('支付方式启用状态必须为布尔值。')
        if bool(body['instructions'].strip()) != bool(body['merchantAccountId'].strip()):
            raise PaymentError('付款说明与收款账户须一起填写，或一起清空。')
        with transaction.atomic():
            policy = OfflinePaymentPolicy.objects.select_for_update().filter(pk=1).first()
            if not policy:
                raise PaymentError('付款配置不可用。', 'PAYMENT_POLICY_UNAVAILABLE', 503)
            actor, denied = require_live(request, 'payment.settings.manage')
            if denied:
                return denied
            if policy.revision != body['expectedRevision']:
                raise PaymentError('付款配置已变化，请刷新重试。', 'REVISION_CONFLICT', 409)
            before = policy_data(policy)
            policy.instructions = body['instructions'].strip()
            policy.merchant_account_id = body['merchantAccountId'].strip()
            policy.wechat_timeout_minutes = body['wechatTimeoutMinutes']
            policy.offline_timeout_minutes = body['offlineTimeoutMinutes']
            if 'offlineEnabled' in body:
                policy.offline_enabled = body['offlineEnabled']
                policy.wechat_enabled = body['wechatEnabled']
            policy.revision += 1
            policy.save()
            audit(request, 'payment.policy.update', 'offline_payment_policy', 1, actor, before=before, after=policy_data(policy))
            return response(request, policy_data(policy))
    except (PaymentError, ValueError) as exc:
        return _failure(request, exc)


@csrf_exempt
def public_offline_policy_view(request):
    bad = method(request, 'GET')
    if bad:
        return bad
    policy = OfflinePaymentPolicy.objects.filter(pk=1).first()
    return response(request, policy_data(policy)) if policy else error(request, 503, 'PAYMENT_POLICY_UNAVAILABLE', '付款配置不可用。')


@csrf_exempt
def payment_report_view(request, order_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    member = resolve_member(request)
    if not member:
        return error(request, 401, 'LOGIN_REQUIRED', '请先完成微信登录。')
    try:
        data, replayed = report_payment(member, order_id, parse_json(request), request_key(request))
        return response(request, data, status=200 if replayed else 201)
    except (PaymentError, ValueError) as exc:
        return _failure(request, exc)


def prepare_reconciliation_view(request, order_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, denied = require(request, 'payment.offline.confirm')
    if denied:
        return denied
    _, read_denied = require(request, 'order.read')
    if read_denied:
        return read_denied
    try:
        data, replayed = prepare_reconciliation(request, actor, order_id, parse_json(request), request_key(request))
        return response(request, data, status=200 if replayed else 201)
    except (PaymentError, ValueError) as exc:
        return _failure(request, exc)


def confirm_reconciliation_view(request, reconciliation_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, denied = require(request, 'payment.offline.confirm')
    if denied:
        return denied
    _, read_denied = require(request, 'order.read')
    if read_denied:
        return read_denied
    try:
        if parse_json(request):
            raise PaymentError('确认时无需重新发送到账资料，请使用已保存的核对记录。')
        result, denied = confirm_reconciliation(request, actor, reconciliation_id)
        if denied:
            return denied
        row = OfflineReconciliation.objects.select_related('order').get(pk=reconciliation_id)
        result['order'] = admin_order_data(row.order)
        return response(request, result)
    except (PaymentError, ValueError) as exc:
        return _failure(request, exc)
