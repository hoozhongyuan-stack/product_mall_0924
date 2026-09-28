"""Authenticated C3 operations. No order or payment state is trusted from clients."""
import re
from uuid import UUID

from django.db import transaction
from django.views.decorators.csrf import csrf_exempt

from common.http import method
from accounts.security import audit, error, parse_json, permissions, require, response
from customers.auth import resolve_member
from .models import Carrier, FulfillmentPolicy, RedeemEvent, Shipment
from .service import (FulfillmentError, auto_confirm_receipts, batch_ship,
                      confirm_receipt, correct_shipment, current_policy,
                      lookup_voucher, redeem, reverse_redemption, ship_order)
from .tracking_service import read_tracking
from orders.models import Order


def _body(request, fields):
    body = parse_json(request)
    if set(body) != set(fields):
        raise FulfillmentError("请求字段不正确。")
    return body


def _key(request):
    try:
        return UUID(request.headers.get("Idempotency-Key", ""))
    except (ValueError, AttributeError) as exc:
        raise FulfillmentError("请提供 UUID 格式的防重复请求标识。") from exc


def _fail(request, exc):
    if isinstance(exc, FulfillmentError):
        return error(request, exc.status, exc.code, str(exc))
    return error(request, 400, "VALIDATION_FAILED", str(exc))


def _carrier_data(row):
    return {"code": row.code, "name": row.name, "enabled": row.enabled, "revision": row.revision}


def carriers_view(request):
    bad = method(request, "GET", "PUT")
    if bad:
        return bad
    actor, denied = require(request, None if request.method == "GET" else "fulfillment.settings.manage")
    if denied:
        return denied
    if request.method == "GET" and not {"fulfillment.read", "fulfillment.ship", "fulfillment.settings.manage"}.intersection(permissions(actor)):
        return error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
    if request.method == "GET":
        return response(request, {"items": [_carrier_data(row) for row in Carrier.objects.order_by("code")[:200]]})
    try:
        body = _body(request, ("code", "name", "enabled", "expectedRevision"))
        code, name = body["code"], body["name"]
        if (not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{2,24}", code) or
                not isinstance(name, str) or not 2 <= len(name.strip()) <= 80 or
                type(body["enabled"]) is not bool or type(body["expectedRevision"]) is not int):
            raise FulfillmentError("快递公司配置不正确。")
        with transaction.atomic():
            row = Carrier.objects.select_for_update().filter(pk=code).first()
            if row is None:
                if body["expectedRevision"] != 0:
                    raise FulfillmentError("配置版本已变化。", "CARRIER_CHANGED", 409)
                row = Carrier.objects.create(code=code, name=name.strip(), enabled=body["enabled"])
            else:
                if row.revision != body["expectedRevision"]:
                    raise FulfillmentError("配置版本已变化。", "CARRIER_CHANGED", 409)
                row.name, row.enabled, row.revision = name.strip(), body["enabled"], row.revision + 1
                row.save(update_fields=["name", "enabled", "revision", "updated_at"])
            audit(request, "fulfillment.carrier.configure", "fulfillment_carrier", row.code, actor=actor,
                  after=_carrier_data(row))
        return response(request, _carrier_data(row))
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


def policy_view(request):
    bad = method(request, "GET", "PUT")
    if bad:
        return bad
    actor, denied = require(request, None if request.method == "GET" else "fulfillment.settings.manage")
    if denied:
        return denied
    if request.method == "GET" and not {"fulfillment.read", "fulfillment.settings.manage"}.intersection(permissions(actor)):
        return error(request, 403, "PERMISSION_DENIED", "当前账号没有此操作权限。")
    try:
        if request.method == "GET":
            policy = current_policy()
        else:
            body = _body(request, ("autoConfirmDays", "expectedRevision"))
            days, revision = body["autoConfirmDays"], body["expectedRevision"]
            if type(days) is not int or not 1 <= days <= 30 or type(revision) is not int:
                raise FulfillmentError("自动收货天数须为 1 至 30 天。")
            with transaction.atomic():
                policy = FulfillmentPolicy.objects.select_for_update().filter(pk=1).first()
                if policy is None or policy.revision != revision:
                    raise FulfillmentError("配置版本已变化。", "POLICY_CHANGED", 409)
                policy.auto_confirm_days = days
                policy.revision += 1
                policy.save(update_fields=["auto_confirm_days", "revision", "updated_at"])
                audit(request, "fulfillment.policy.configure", "fulfillment_policy", 1, actor=actor,
                      after={"autoConfirmDays": days, "revision": policy.revision})
        return response(request, {"autoConfirmDays": policy.auto_confirm_days, "revision": policy.revision})
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


def shipment_view(request, order_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, denied = require(request, "fulfillment.ship")
    if denied:
        return denied
    try:
        body = _body(request, ("carrierCode", "trackingNo", "expectedRevision"))
        data = ship_order(order_id, actor, body["carrierCode"], body["trackingNo"],
                          body["expectedRevision"], _key(request))
        audit(request, "fulfillment.ship", "order", order_id, actor=actor,
              after={"carrierCode": body["carrierCode"]})
        return response(request, data)
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


def shipment_correct_view(request, order_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, denied = require(request, "fulfillment.ship")
    if denied:
        return denied
    try:
        body = _body(request, ("carrierCode", "trackingNo", "reason", "expectedRevision"))
        data = correct_shipment(order_id, actor, body["carrierCode"], body["trackingNo"],
                                body["reason"], body["expectedRevision"])
        audit(request, "fulfillment.shipment.correct", "order", order_id, actor=actor,
              after={"reason": body["reason"]})
        return response(request, data)
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


def batch_ship_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, denied = require(request, "fulfillment.ship")
    if denied:
        return denied
    try:
        body = _body(request, ("items",))
        data = batch_ship(body["items"], actor)
        audit(request, "fulfillment.ship.batch", "order", actor=actor,
              after={"succeeded": data["succeeded"], "failed": data["failed"]})
        return response(request, data)
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


def lookup_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, denied = require(request, "fulfillment.redeem")
    if denied:
        return denied
    try:
        body = _body(request, ("code",))
        data = lookup_voucher(body["code"], actor, request.META.get("REMOTE_ADDR", "unknown"))
        audit(request, "fulfillment.redeem.lookup", "redeem_voucher", data["voucherId"], actor=actor)
        return response(request, data)
    except (FulfillmentError, ValueError) as exc:
        audit(request, "fulfillment.redeem.lookup", "redeem_voucher", actor=actor, result="DENIED")
        return _fail(request, exc)


def use_view(request, voucher_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, denied = require(request, "fulfillment.redeem")
    if denied:
        return denied
    try:
        body = _body(request, ("quantity", "expectedRevision"))
        data = redeem(voucher_id, actor, body["quantity"], body["expectedRevision"], _key(request))
        audit(request, "fulfillment.redeem.use", "redeem_voucher", voucher_id, actor=actor,
              after={"quantity": body["quantity"], "eventId": data["eventId"]})
        return response(request, data)
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


def reverse_view(request, event_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, denied = require(request, "fulfillment.redeem.reverse")
    if denied:
        return denied
    try:
        body = _body(request, ("reason", "expectedRevision"))
        data = reverse_redemption(event_id, actor, body["reason"], body["expectedRevision"])
        audit(request, "fulfillment.redeem.reverse", "redeem_event", event_id, actor=actor,
              after={"reason": body["reason"], "reversalEventId": data["eventId"]})
        return response(request, data)
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


@csrf_exempt
def confirm_receipt_view(request, order_id):
    bad = method(request, "POST")
    if bad:
        return bad
    member = resolve_member(request)
    if member is None:
        return error(request, 401, "LOGIN_REQUIRED", "请先完成微信登录。")
    try:
        _body(request, ())
        return response(request, confirm_receipt(member, order_id))
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


def tracking_view(request, order_id):
    bad = method(request, "GET")
    if bad:
        return bad
    member = resolve_member(request)
    if member is None:
        return error(request, 401, "LOGIN_REQUIRED", "请先完成微信登录。")
    order = Order.objects.filter(pk=order_id, member_id=member.id, status="PAID").first()
    if order is None:
        return error(request, 404, "ORDER_NOT_FOUND", "订单不存在或尚未发货。")
    shipment = Shipment.objects.filter(order=order).first()
    if shipment is None:
        return error(request, 404, "NOT_SHIPPED", "订单尚未发货。")
    result = response(request, read_tracking(shipment.id))
    result["Cache-Control"] = "private, no-store"
    return result
