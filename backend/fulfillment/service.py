"""Short PostgreSQL transactions for shipment, receipt and voucher redemption."""
import hashlib
import hmac
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from accounts.security import permissions
from orders.models import Order
from .codes import code_digest, new_nonce, normalized_code, voucher_code
from .models import (Carrier, FulfillmentPolicy, OrderFulfillmentSnapshot,
                     RedeemEvent, RedeemLookupFailure, RedeemVoucher,
                     Shipment, ShipmentCorrection, VoucherRefundEvent)
from .read import fulfillment_data


class FulfillmentError(ValueError):
    def __init__(self, message, code="VALIDATION_FAILED", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _permit(actor, code):
    if not actor or not actor.enabled or code not in permissions(actor):
        raise FulfillmentError("当前账号没有此操作权限。", "PERMISSION_DENIED", 403)


def _order_data(order, *, include_voucher_code=False):
    from orders.service import order_data
    return order_data(order, include_voucher_code=include_voucher_code)


def _locked_order(order_id):
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if order is None:
        raise FulfillmentError("订单不存在。", "ORDER_NOT_FOUND", 404)
    from customers.consumption import lock_consumption_member
    lock_consumption_member(order.member_id)
    return order


def _revision(order, expected):
    if type(expected) is not int or expected != order.revision:
        raise FulfillmentError("订单已变化，请刷新后重试。", "ORDER_CHANGED", 409)


def _paid(order):
    if order.status != Order.Status.PAID:
        raise FulfillmentError("订单尚未确认收款。", "ORDER_NOT_PAID", 409)


def _tracking(value):
    if not isinstance(value, str) or not 4 <= len(value.strip()) <= 80:
        raise FulfillmentError("运单号须为 4 至 80 个字符。")
    value = value.strip()
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise FulfillmentError("运单号只能使用可打印英文字符。")
    return value


def _carrier(code):
    if not isinstance(code, str) or len(code) > 24:
        raise FulfillmentError("快递公司不正确。")
    carrier = Carrier.objects.filter(code=code.strip().upper(), enabled=True).first()
    if carrier is None:
        raise FulfillmentError("快递公司未启用。", "CARRIER_UNAVAILABLE", 409)
    return carrier


def current_policy():
    # The singleton migration seeds production; test database flushes and a
    # fresh local database may remove data rows, so restore the documented default.
    return FulfillmentPolicy.objects.get_or_create(pk=1, defaults={
        "auto_confirm_days": 10, "revision": 1})[0]


def snapshot_order_policy_locked(order):
    """Invoked by order submission after creating the order, within that transaction."""
    if not connection.in_atomic_block:
        raise RuntimeError("履约时限快照必须在订单事务内。")
    policy = current_policy()
    return OrderFulfillmentSnapshot.objects.create(
        order=order, auto_confirm_days=policy.auto_confirm_days,
        policy_revision=policy.revision)


def issue_paid_vouchers_locked(order):
    """Order-owned paid hook; safe on repeated verified-payment notifications."""
    if not connection.in_atomic_block or order.status != Order.Status.PAID:
        raise RuntimeError("核销凭证只可在已付款订单事务内签发。")
    for line in order.lines.filter(fulfillment_kind="REDEEM").order_by("id"):
        if RedeemVoucher.objects.filter(order_line=line).exists():
            continue
        nonce = new_nonce()
        code = voucher_code(line.id, nonce)
        RedeemVoucher.objects.create(order_line=line, nonce=nonce, code_digest=code_digest(code),
                                     valid_until=getattr(line, "redeem_valid_until", None))


def ship_order(order_id, actor, carrier_code, tracking_no, expected_revision, request_key):
    _permit(actor, "fulfillment.ship")
    if not isinstance(request_key, uuid.UUID):
        raise FulfillmentError("请提供 UUID 格式的防重复请求标识。")
    tracking = _tracking(tracking_no)
    with transaction.atomic():
        order = _locked_order(order_id)
        existing = Shipment.objects.select_for_update().filter(order=order).first()
        if existing:
            if (existing.request_key == request_key and existing.carrier_code == carrier_code
                    and existing.tracking_no == tracking):
                return _order_data(order)
            raise FulfillmentError("该订单已经发货。", "ALREADY_SHIPPED", 409)
        _paid(order)
        if getattr(order, "delivery_mode", "") in ("PICKUP", "DELIVERY"):
            raise FulfillmentError("自提或同城配送订单不能快递发货。", "DELIVERY_MODE_MISMATCH", 409)
        _revision(order, expected_revision)
        carrier = _carrier(carrier_code)
        lines = list(order.lines.filter(fulfillment_kind="SHIP"))
        if not lines:
            raise FulfillmentError("订单没有待发货商品。", "NO_SHIPPING_LINES", 409)
        from aftersales.guards import assert_shippable_locked
        lines = _aftersale_guard(assert_shippable_locked, order, lines)
        warehouses = {line.warehouse_id for line in lines}
        if len(warehouses) != 1:
            raise FulfillmentError("发货仓快照不一致，需人工核查。", "WAREHOUSE_MISMATCH", 409)
        snapshot = OrderFulfillmentSnapshot.objects.filter(order=order).first()
        if snapshot is None:
            raise FulfillmentError("订单缺少自动收货时限快照。", "FULFILLMENT_SNAPSHOT_MISSING", 409)
        now = timezone.now()
        shipment = Shipment.objects.create(order=order, carrier=carrier, carrier_code=carrier.code,
                                carrier_name=carrier.name, tracking_no=tracking,
                                warehouse_id=next(iter(warehouses)), shipped_by=actor,
                                shipped_at=now, auto_confirm_days_snapshot=snapshot.auto_confirm_days,
                                auto_confirm_at=now + timedelta(days=snapshot.auto_confirm_days),
                                request_key=request_key)
        from notifications.service import ORDER_SHIPPED, record_event
        record_event(ORDER_SHIPPED, shipment.id, order.member_id, occurred_at=shipment.shipped_at)
        order.revision += 1
        order.save(update_fields=["revision"])
        return _order_data(order)


def correct_shipment(order_id, actor, carrier_code, tracking_no, reason, expected_revision):
    _permit(actor, "fulfillment.ship")
    tracking = _tracking(tracking_no)
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        raise FulfillmentError("更正原因须为 5 至 500 字。")
    with transaction.atomic():
        order = _locked_order(order_id)
        _paid(order)
        _revision(order, expected_revision)
        from customers.consumption import lock_consumption_member
        lock_consumption_member(order.member_id)
        shipment = Shipment.objects.select_for_update().filter(order=order).first()
        if shipment is None:
            raise FulfillmentError("该订单尚未发货。", "NOT_SHIPPED", 409)
        carrier = _carrier(carrier_code)
        if shipment.carrier_code == carrier.code and shipment.tracking_no == tracking:
            raise FulfillmentError("更正内容与原运单相同。")
        ShipmentCorrection.objects.create(shipment=shipment, old_carrier_code=shipment.carrier_code,
            old_carrier_name=shipment.carrier_name, old_tracking_no=shipment.tracking_no,
            new_carrier_code=carrier.code, new_carrier_name=carrier.name, new_tracking_no=tracking,
            reason=reason.strip(), actor=actor)
        shipment.carrier = carrier
        shipment.carrier_code = carrier.code
        shipment.carrier_name = carrier.name
        shipment.tracking_no = tracking
        shipment.revision += 1
        shipment.save(update_fields=["carrier", "carrier_code", "carrier_name", "tracking_no", "revision"])
        order.revision += 1
        order.save(update_fields=["revision"])
        return _order_data(order)


def confirm_receipt(member, order_id):
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id, member=member).first()
        if order is None:
            raise FulfillmentError("订单不存在。", "ORDER_NOT_FOUND", 404)
        _paid(order)
        from customers.consumption import lock_consumption_member
        lock_consumption_member(order.member_id)
        shipment = Shipment.objects.select_for_update().filter(order=order).first()
        if shipment is None:
            raise FulfillmentError("订单尚未发货。", "NOT_SHIPPED", 409)
        if shipment.confirmed_at is None:
            shipment.confirmed_at = timezone.now()
            shipment.confirmed_by_member = True
            shipment.revision += 1
            shipment.save(update_fields=["confirmed_at", "confirmed_by_member", "revision"])
            order.revision += 1
            order.save(update_fields=["revision"])
        from orders.benefit_lifecycle import reconcile_benefits_locked
        reconcile_benefits_locked(order, source_ref="receipt:" + str(order.id))
        return _order_data(order, include_voucher_code=True)


def auto_confirm_receipts(batch_size=100):
    if type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise FulfillmentError("批量大小不正确。")
    ids = list(Shipment.objects.filter(confirmed_at__isnull=True,
                                       auto_confirm_at__lte=timezone.now())
               .order_by("auto_confirm_at").values_list("order_id", flat=True)[:batch_size])
    count = 0
    for order_id in ids:
        with transaction.atomic():
            order = _locked_order(order_id)
            shipment = Shipment.objects.select_for_update().filter(order=order).first()
            if shipment and shipment.confirmed_at is None and shipment.auto_confirm_at <= timezone.now() \
                    and order.status == Order.Status.PAID:
                shipment.confirmed_at = timezone.now()
                shipment.revision += 1
                shipment.save(update_fields=["confirmed_at", "revision"])
                order.revision += 1
                order.save(update_fields=["revision"])
                from orders.benefit_lifecycle import reconcile_benefits_locked
                reconcile_benefits_locked(order, source_ref="auto-receipt:" + str(order.id))
                count += 1
    return count


def _remaining(voucher):
    return voucher.order_line.quantity - voucher.redeemed_quantity - voucher.voided_quantity


def _locked_voucher(voucher_id):
    # Lock order first, matching payment and shipment transactions.
    initial = RedeemVoucher.objects.filter(pk=voucher_id).values_list("order_line__order_id", flat=True).first()
    if initial is None:
        raise FulfillmentError("核销凭证不存在。", "VOUCHER_NOT_FOUND", 404)
    order = _locked_order(initial)
    voucher = RedeemVoucher.objects.select_for_update().select_related("order_line").filter(pk=voucher_id).first()
    if voucher is None:
        raise FulfillmentError("核销凭证不存在。", "VOUCHER_NOT_FOUND", 404)
    return order, voucher


def redeem(voucher_id, actor, quantity, expected_revision, request_key):
    _permit(actor, "fulfillment.redeem")
    if type(quantity) is not int or not 1 <= quantity <= 9999 or not isinstance(request_key, uuid.UUID):
        raise FulfillmentError("核销数量或防重复标识不正确。")
    with transaction.atomic():
        order, voucher = _locked_voucher(voucher_id)
        replay = RedeemEvent.objects.filter(voucher=voucher, request_key=request_key, kind="USE").first()
        if replay:
            if replay.quantity != quantity or replay.actor_id != actor.id:
                raise FulfillmentError("防重复标识对应不同请求。", "IDEMPOTENCY_CONFLICT", 409)
            return {"eventId": str(replay.id), "remainingQuantity": replay.remaining_quantity,
                    "voucherRevision": voucher.revision, "replayed": True}
        _paid(order)
        if type(expected_revision) is not int or expected_revision != voucher.revision:
            raise FulfillmentError("凭证数量已变化，请刷新后重试。", "VOUCHER_CHANGED", 409)
        if not voucher.valid_until:
            raise FulfillmentError("商品核销有效期尚未明确，暂不可核销。", "VALIDITY_MISSING", 409)
        if voucher.valid_until < timezone.localdate():
            raise FulfillmentError("核销凭证已过期。", "VOUCHER_EXPIRED", 409)
        remaining = _remaining(voucher)
        from aftersales.guards import reserved_unredeemed_quantity
        available = remaining - reserved_unredeemed_quantity(voucher.order_line_id)
        if quantity > available:
            raise FulfillmentError("核销数量超过剩余数量。", "QUANTITY_EXCEEDED", 409)
        voucher.redeemed_quantity += quantity
        voucher.revision += 1
        voucher.save(update_fields=["redeemed_quantity", "revision"])
        event = RedeemEvent.objects.create(voucher=voucher, kind="USE", quantity=quantity,
                                           remaining_quantity=remaining-quantity, actor=actor,
                                           request_key=request_key)
        order.revision += 1
        order.save(update_fields=["revision"])
        from orders.benefit_lifecycle import reconcile_benefits_locked
        reconcile_benefits_locked(order, source_ref="redeem:" + str(event.id))
        return {"eventId": str(event.id), "remainingQuantity": remaining-quantity,
                "voucherRevision": voucher.revision, "replayed": False}


def reverse_redemption(event_id, actor, reason, expected_revision):
    _permit(actor, "fulfillment.redeem.reverse")
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        raise FulfillmentError("撤销原因须为 5 至 500 字。")
    original = RedeemEvent.objects.filter(pk=event_id).values_list("voucher_id", flat=True).first()
    if original is None:
        raise FulfillmentError("核销记录不存在。", "REDEMPTION_NOT_FOUND", 404)
    with transaction.atomic():
        order, voucher = _locked_voucher(original)
        _paid(order)
        event = RedeemEvent.objects.select_for_update().filter(pk=event_id, kind="USE", voucher=voucher).first()
        if event is None:
            raise FulfillmentError("核销记录不存在。", "REDEMPTION_NOT_FOUND", 404)
        reversal = RedeemEvent.objects.filter(original_use=event).first()
        if reversal:
            return {"eventId": str(reversal.id), "remainingQuantity": reversal.remaining_quantity,
                    "voucherRevision": voucher.revision, "replayed": True}
        if type(expected_revision) is not int or expected_revision != voucher.revision:
            raise FulfillmentError("凭证数量已变化，请刷新后重试。", "VOUCHER_CHANGED", 409)
        from aftersales.guards import assert_reversal_allowed_locked
        _aftersale_guard(assert_reversal_allowed_locked, voucher.order_line)
        if voucher.redeemed_quantity < event.quantity:
            raise FulfillmentError("核销记录数量异常。", "REDEMPTION_CONFLICT", 409)
        voucher.redeemed_quantity -= event.quantity
        voucher.revision += 1
        voucher.save(update_fields=["redeemed_quantity", "revision"])
        remaining = _remaining(voucher)
        reversal = RedeemEvent.objects.create(voucher=voucher, kind="REVERSE", quantity=event.quantity,
                                               remaining_quantity=remaining, actor=actor,
                                               original_use=event, reason=reason.strip())
        order.revision += 1
        order.save(update_fields=["revision"])
        from orders.benefit_lifecycle import reconcile_benefits_locked
        reconcile_benefits_locked(order, source_ref="reverse:" + str(reversal.id))
        return {"eventId": str(reversal.id), "remainingQuantity": remaining,
                "voucherRevision": voucher.revision, "replayed": False}


def lookup_voucher(code, actor, source="unknown"):
    _permit(actor, "fulfillment.redeem")
    normalized = normalized_code(code)
    source_digest = hmac.new(settings.SECRET_KEY.encode(), str(source).encode(), hashlib.sha256).hexdigest()
    with transaction.atomic():
        # Serialize guess attempts for the same staff account across devices.
        from accounts.models import AdminAccount
        AdminAccount.objects.select_for_update().get(pk=actor.id)
        window = timezone.now() - timedelta(minutes=15)
        failures = RedeemLookupFailure.objects.filter(actor=actor, occurred_at__gte=window).count()
        if failures >= 5:
            raise FulfillmentError("查询过于频繁，请稍后重试。", "LOOKUP_RATE_LIMITED", 429)
        voucher = (RedeemVoucher.objects.select_related("order_line__order")
                   .filter(code_digest=code_digest(normalized)).first() if normalized else None)
        if voucher is None:
            RedeemLookupFailure.objects.create(actor=actor, source_digest=source_digest)
            result = None
        else:
            order = voucher.order_line.order
            remaining = _remaining(voucher)
            from aftersales.guards import reserved_unredeemed_quantity
            held = reserved_unredeemed_quantity(voucher.order_line_id)
            available = max(0, remaining - held)
            status = ("UNPAID" if order.status != Order.Status.PAID else
                      "VALIDITY_MISSING" if not voucher.valid_until else
                      "EXPIRED" if voucher.valid_until < timezone.localdate() else
                      "REFUNDED" if voucher.voided_quantity == voucher.order_line.quantity else
                      "FULLY_REDEEMED" if remaining == 0 else
                      "AFTER_SALE" if available == 0 else "READY")
            recent = list(RedeemEvent.objects.filter(voucher=voucher)
                          .select_related("actor", "reversal").order_by("-occurred_at", "-id")[:20])
            events = [{"eventId": str(event.id), "kind": event.kind, "quantity": event.quantity,
                       "remainingQuantity": event.remaining_quantity,
                       "actorName": event.actor.display_name, "occurredAt": event.occurred_at.isoformat(),
                       "reversedAt": (event.reversal.occurred_at.isoformat()
                                      if event.kind == "USE" and hasattr(event, "reversal") else None),
                       "reason": event.reason or None} for event in recent]
            result = {"voucherId": str(voucher.id), "orderId": str(order.id),
                      "orderNo": order.order_no, "orderLineId": str(voucher.order_line_id),
                      "name": voucher.order_line.product_name, "quantity": voucher.order_line.quantity,
                      "redeemedQuantity": voucher.redeemed_quantity,
                      "voidedQuantity": voucher.voided_quantity, "remainingQuantity": remaining,
                      "heldQuantity": held, "availableQuantity": available,
                      "validUntil": voucher.valid_until.isoformat() if voucher.valid_until else None,
                      "revision": voucher.revision, "status": status,
                      "events": events, "eventCount": RedeemEvent.objects.filter(voucher=voucher).count()}
    if result is None:
        raise FulfillmentError("凭证无效。", "VOUCHER_NOT_FOUND", 404)
    return result


def batch_ship(items, actor):
    _permit(actor, "fulfillment.ship")
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise FulfillmentError("批量发货须包含 1 至 50 单。")
    results = []
    seen = set()
    for item in items:
        order_id = item.get("orderId") if isinstance(item, dict) else None
        try:
            if not isinstance(item, dict) or set(item) != {"orderId", "carrierCode", "trackingNo", "expectedRevision", "requestKey"}:
                raise FulfillmentError("单笔发货字段不正确。")
            order_uuid = uuid.UUID(str(order_id))
            key = uuid.UUID(str(item["requestKey"]))
            if order_uuid in seen:
                raise FulfillmentError("批次内订单重复。", "DUPLICATE_ORDER", 409)
            seen.add(order_uuid)
            data = ship_order(order_uuid, actor, item["carrierCode"], item["trackingNo"],
                              item["expectedRevision"], key)
            results.append({"orderId": str(order_uuid), "success": True, "order": data})
        except (FulfillmentError, ValueError) as exc:
            code = exc.code if isinstance(exc, FulfillmentError) else "VALIDATION_FAILED"
            results.append({"orderId": str(order_id) if order_id is not None else None,
                            "success": False, "error": {"code": code, "message": str(exc)}})
    return {"results": results, "succeeded": sum(row["success"] for row in results),
            "failed": sum(not row["success"] for row in results)}


def _aftersale_guard(guard, *args):
    from aftersales.service import AfterSaleError
    try:
        return guard(*args)
    except AfterSaleError as exc:
        raise FulfillmentError(str(exc), exc.code, exc.status) from exc


def void_refunded_quantity_locked(line, quantity, case_id):
    """Refund coordinator holds the order lock; retire only unused capacity once."""
    if not connection.in_atomic_block:
        raise RuntimeError("退款核销失效必须在订单事务内。")
    if type(quantity) is not int or not 1 <= quantity <= line.quantity or not isinstance(case_id, uuid.UUID):
        raise FulfillmentError("退款核销数量或售后标识不正确。")
    voucher = (RedeemVoucher.objects.select_for_update().select_related("order_line")
               .filter(order_line=line).first())
    if voucher is None:
        raise FulfillmentError("历史订单缺少核销凭证，需人工核查。", "VOUCHER_MISSING", 409)
    existing = VoucherRefundEvent.objects.filter(case_id=case_id).first()
    if existing:
        if existing.voucher_id != voucher.id or existing.quantity != quantity:
            raise FulfillmentError("售后退款对应不同核销数量。", "IDEMPOTENCY_CONFLICT", 409)
        return existing
    if quantity > _remaining(voucher):
        raise FulfillmentError("退款数量超过未核销余量。", "QUANTITY_EXCEEDED", 409)
    event = VoucherRefundEvent.objects.create(voucher=voucher, case_id=case_id, quantity=quantity)
    voucher.voided_quantity += quantity
    voucher.revision += 1
    voucher.save(update_fields=["voided_quantity", "revision"])
    return event
