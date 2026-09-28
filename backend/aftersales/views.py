"""D1 member bearer and admin CSRF/session endpoints."""

import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from common.http import offset_response, method
from accounts.security import (
    error,
    response,
    parse_json,
    require,
    confirm_action,
    audit,
)
from customers.auth import resolve_member
from .models import AfterSaleCase, AfterSaleEvent
from .service import (
    AfterSaleError,
    apply_case,
    withdraw_case,
    review_case,
    _locked_case,
)
from .api_read import options, preview, case_data, list_cases


def fail(request, exc):
    return error(
        request,
        getattr(exc, "status", 400),
        getattr(exc, "code", "VALIDATION_FAILED"),
        str(exc),
    )


def result(request, data, status=200, **kwargs):
    r = response(request, data, status=status, **kwargs)
    r["Cache-Control"] = "private, no-store"
    return r


def member(request):
    m = resolve_member(request)
    return (
        (m, None)
        if m
        else (None, error(request, 401, "LOGIN_REQUIRED", "请先完成微信登录。"))
    )


def key(request):
    try:
        return uuid.UUID(request.headers.get("Idempotency-Key", ""))
    except (ValueError, AttributeError):
        raise AfterSaleError("请提供 UUID 防重复标识。")


def body_fields(request, fields):
    b = parse_json(request)
    if set(b) != fields:
        raise AfterSaleError("请求字段不正确。")
    return b


def limited(m):
    if (
        AfterSaleEvent.objects.filter(
            case__member=m, occurred_at__gte=timezone.now() - timedelta(minutes=15)
        ).count()
        >= 60
    ):
        raise AfterSaleError("售后操作过多，请稍后重试。", "RATE_LIMITED", 429)


@csrf_exempt
def options_view(request, order_id):
    bad = method(request, "GET")
    if bad:
        return bad
    m, bad = member(request)
    if bad:
        return bad
    try:
        return result(request, options(m, order_id))
    except ValueError as exc:
        return fail(request, exc)


@csrf_exempt
def preview_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    m, bad = member(request)
    if bad:
        return bad
    try:
        b = body_fields(request, {"lineId", "kind", "redemptionScope", "quantity"})
        try:
            line_id = uuid.UUID(b["lineId"])
        except (ValueError, TypeError, AttributeError):
            raise AfterSaleError("订单项标识不正确。")
        return result(
            request, preview(m, line_id, b["kind"], b["redemptionScope"], b["quantity"])
        )
    except ValueError as exc:
        return fail(request, exc)


@csrf_exempt
def member_cases_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    m, bad = member(request)
    if bad:
        return bad
    try:
        if request.method == "GET":
            return offset_response(request, list_cases(request.GET, m), respond=result)
        b = body_fields(
            request, {"lineId", "kind", "redemptionScope", "quantity", "reason"}
        )
        k = key(request)
        prior = AfterSaleCase.objects.filter(member=m, request_key=k).exists()
        if not prior:
            limited(m)
        c = apply_case(
            m,
            b["lineId"],
            b["kind"],
            b["quantity"],
            b["reason"],
            k,
            b["redemptionScope"],
        )
        return result(request, case_data(c), status=200 if prior else 201)
    except ValueError as exc:
        return fail(request, exc)


@csrf_exempt
def member_detail_view(request, case_id):
    bad = method(request, "GET")
    if bad:
        return bad
    m, bad = member(request)
    if bad:
        return bad
    c = (
        AfterSaleCase.objects.select_related("order_line__order", "return_acceptance__actor")
        .filter(pk=case_id, member=m)
        .first()
    )
    return (
        result(request, case_data(c))
        if c
        else error(request, 404, "AFTERSALE_NOT_FOUND", "申请不存在。")
    )


@csrf_exempt
def withdraw_view(request, case_id):
    bad = method(request, "POST")
    if bad:
        return bad
    m, bad = member(request)
    if bad:
        return bad
    try:
        b = body_fields(request, {"expectedRevision"})
        limited(m)
        return result(
            request, case_data(withdraw_case(case_id, m, b["expectedRevision"]))
        )
    except ValueError as exc:
        return fail(request, exc)


def admin_cases_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    a, bad = require(request, "aftersale.read")
    if bad:
        return bad
    try:
        return offset_response(request, list_cases(request.GET, actor=a), respond=result)
    except ValueError as exc:
        return fail(request, exc)


def admin_detail_view(request, case_id):
    bad = method(request, "GET")
    if bad:
        return bad
    a, bad = require(request, "aftersale.read")
    if bad:
        return bad
    c = (
        AfterSaleCase.objects.select_related("order_line__order", "member", "return_acceptance__actor")
        .filter(pk=case_id)
        .first()
    )
    return (
        result(request, case_data(c, admin=True, actor=a))
        if c
        else error(request, 404, "AFTERSALE_NOT_FOUND", "申请不存在。")
    )


def review_view(request, case_id):
    bad = method(request, "POST")
    if bad:
        return bad
    a, bad = require(request, "aftersale.review")
    if bad:
        return bad
    _, bad = require(request, "aftersale.read")
    if bad:
        return bad
    try:
        b = body_fields(request, {"approve", "reason", "expectedRevision"})
        with transaction.atomic():
            _, _, c = _locked_case(case_id)
            if (
                type(b["expectedRevision"]) is not int
                or c.revision != b["expectedRevision"]
                or c.status != "PENDING_REVIEW"
            ):
                raise AfterSaleError("申请已变化，请刷新。", "AFTERSALE_CHANGED", 409)
            denied = confirm_action(request, a, "aftersale.review", c.id, c.revision)
            if denied:
                return denied
            c = review_case(c.id, a, b["approve"], b["reason"], b["expectedRevision"])
            audit(
                request,
                "aftersale.review",
                "aftersale_case",
                c.id,
                a,
                after={"status": c.status, "amountFen": c.amount_fen},
            )
        return result(request, case_data(c, admin=True, actor=a))
    except ValueError as exc:
        return fail(request, exc)
