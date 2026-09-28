"""B4 order transaction. Public submission remains gated until settlement is complete."""

import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.utils import timezone

from catalog.models import Category, Product, Sku
from catalog.quote_access import quote_catalog_rows
from benefits.service import (BenefitError, assert_order_benefits, consume_benefits,
                              release_benefits, reserve_benefits)
from checkout.models import CheckoutQuote
from customers.models import CustomerAddress, Member
from inventory.models import Warehouse
from inventory.reservations import (ReservationError, assert_order_reservations,
                                    consume_order_reservations, release_order_reservations,
                                    reserve_order_lines)
from payments.availability import payment_method_enabled
from shipping.service import ShippingUnavailable, current_shipping_policy, shipping_fee_for_lines

from .models import Order, OrderIdempotency, OrderLine


class OrderError(ValueError):
    def __init__(self, message, code="VALIDATION_FAILED", status=400, details=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or []


def _uuid(value, label):
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    raise OrderError(f"{label}格式不正确。")


def _request(body):
    if set(body) != {"quoteId", "paymentMethod"}:
        raise OrderError("订单字段不正确；优惠券和积分须在报价时选择。")
    quote_id = _uuid(body["quoteId"], "报价 ID")
    method = body["paymentMethod"]
    if method not in {Order.PaymentMethod.OFFLINE, Order.PaymentMethod.WECHAT}:
        raise OrderError("支付方式不正确。")
    return quote_id, method


def _key_lock(member_id, key):
    digest = hashlib.sha256(f"CREATE_ORDER:{member_id}:{key}".encode()).digest()
    lock_key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])


def _business_digest(quote, method):
    source = {
        "method": method,
        "addressId": str(quote.address_id) if quote.address_id else None,
        "shippingFeeFen": quote.shipping_fee_fen,
        "shippingPolicyRevision": quote.shipping_policy_revision,
        "couponId": str(quote.coupon_id) if quote.coupon_id else None,
        "couponDiscountFen": quote.coupon_discount_fen,
        "benefitPolicySnapshot": quote.benefit_policy_snapshot,
        "pointsToUse": quote.points_to_use,
        "pointsDiscountFen": quote.points_discount_fen,
        "lines": sorted((line["skuId"], line["quantity"], line["unitPriceFen"],
                         line["ratio"], line["saleUnit"]) for line in quote.lines),
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def order_data(order, *, include_voucher_code=False):
    rows = list(order.lines.select_related("sku", "warehouse").order_by("sku_id"))
    from payments.wechat_status import payment_status
    from fulfillment.read import fulfillment_data
    fulfillment = fulfillment_data(order, include_code=include_voucher_code)
    from benefits.lifecycle import order_benefit_data
    return {
        **payment_status(order),
        "orderBenefits": order_benefit_data(order.id),
        "fulfillmentStatus": fulfillment["fulfillmentStatus"],
        "afterSaleSummary": fulfillment["afterSaleSummary"],
        "shipment": fulfillment["shipment"],
        "orderId": str(order.id), "orderNo": order.order_no, "status": order.status,
        "orderKind": order.order_kind, "exchangePoints": order.points_to_use if order.order_kind == "POINTS" else 0,
        "revision": order.revision, "paymentInstructions": order.payment_instructions or None,
        "paymentReviewStatus": ("PAID" if order.status == "PAID" else "CLOSED" if order.status == "CLOSED"
                                else "PENDING_REVIEW" if order.payment_reports.exists() else "UNREPORTED"),
        "paymentReports": [{"reportId": str(report.id), "note": report.note, "reportedAt": report.reported_at.isoformat()}
                           for report in order.payment_reports.order_by("reported_at")[:20]],
        "paymentMethod": order.payment_method, "goodsTotalFen": order.goods_total_fen,
        "shippingFeeFen": order.shipping_fee_fen, "payableFen": order.payable_fen,
        "couponId": str(order.coupon_id) if order.coupon_id else None,
        "couponDiscountFen": order.coupon_discount_fen,
        "pointsToUse": order.points_to_use,
        "pointsDiscountFen": order.points_discount_fen,
        "expiresAt": order.expires_at.isoformat(), "createdAt": order.created_at.isoformat(),
        "paidAt": order.paid_at.isoformat() if order.paid_at else None,
        "closedAt": order.closed_at.isoformat() if order.closed_at else None,
        "closeReason": order.close_reason or None,
        "address": order.address_snapshot or None,
        "items": [{"orderLineId": str(line.id), "skuId": str(line.sku_id),
                   "skuCode": line.sku_code, "productId": str(line.product_id),
                   "name": line.product_name, "specs": line.specs_snapshot,
                   "fulfillmentKind": line.fulfillment_kind,
                   "warehouseId": str(line.warehouse_id), "quantity": line.quantity,
                   "saleUnit": line.sale_unit, "ratio": line.ratio,
                   "baseQuantity": line.base_quantity,
                   "unitVersionId": str(line.unit_version_id),
                   "pointsUnitPrice": line.points_unit_price, "pointsTotal": line.points_total,
                   "unitPriceFen": line.unit_price_fen, "goodsAmountFen": line.goods_amount_fen,
                   "couponDiscountFen": line.coupon_discount_fen,
                   "pointsDiscountFen": line.points_discount_fen,
                   "payableFen": line.payable_fen,
                   "redeemValidUntil": line.redeem_valid_until.isoformat() if line.redeem_valid_until else None,
                   "fulfillment": fulfillment["items"].get(str(line.id))} for line in rows],
    }


def _address_snapshot(quote, member, needs_shipping):
    if not needs_shipping:
        return {}
    if not quote.address_id:
        raise OrderError("请选择收货地址。", "ADDRESS_REQUIRED", 409)
    address = CustomerAddress.objects.filter(id=quote.address_id, member_id=member.id, active=True).first()
    if not address:
        raise OrderError("收货地址已失效，请重新报价。", "ADDRESS_CHANGED", 409)
    return {"recipientName": address.recipient_name, "phone": address.phone,
            "province": address.province, "city": address.city, "district": address.district,
            "detail": address.detail}


def _current_lines(quote, member):
    if not quote.ready or not quote.lines or len(quote.lines) > 50:
        raise OrderError("商品已变化，请重新报价。", "QUOTE_NOT_READY", 409)
    if any(line.get("priceChanged") for line in quote.lines):
        raise OrderError("请先确认新价格并重新报价。", "PRICE_CONFIRMATION_REQUIRED", 409)
    requested = {UUID(line["skuId"]): line for line in quote.lines}
    if len(requested) != len(quote.lines):
        raise OrderError("报价商品重复，请重新报价。", "QUOTE_NOT_READY", 409)
    # Existing inbound/outbound transactions lock SKU before balance. Keep
    # this order to avoid a cross-module deadlock, then recheck stock under
    # the balance locks.
    product_ids = {UUID(line["productId"]) for line in quote.lines}
    products = list(Product.objects.select_related("category").select_for_update(of=("self",)).filter(
        id__in=product_ids).order_by("id"))
    if len(products) != len(product_ids):
        raise OrderError("商品已失效，请重新报价。", "QUOTE_CHANGED", 409)
    category_ids = {product.category_id for product in products}
    category_ids.update(product.category.parent_id for product in products if product.category.parent_id)
    list(Category.objects.select_for_update().filter(id__in=category_ids).order_by("id"))
    skus = {sku.id: sku for sku in Sku.objects.select_for_update(of=("self",)).filter(
        id__in=requested).select_related("current_unit", "product").order_by("id")}
    current = quote_catalog_rows(list(requested), member)
    if len(skus) != len(requested):
        raise OrderError("商品已失效，请重新报价。", "QUOTE_CHANGED", 409)
    result = []
    for sku_id in sorted(requested):
        snapshot = requested[sku_id]
        sku = skus[sku_id]
        row = current.get(sku_id)
        unit = sku.current_unit
        if (not row or not row["onSale"] or not unit or
                str(sku.product_id) != snapshot["productId"] or
                row["fulfillmentKind"] != snapshot["fulfillmentKind"] or
                row["ratio"] != snapshot["ratio"] or
                row["saleUnit"] != snapshot["saleUnit"]):
            raise OrderError("商品或单位已变化，请重新报价。", "QUOTE_CHANGED", 409)
        if (row["fulfillmentKind"] == "REDEEM" and
                (not row["redeemValidUntil"] or row["redeemValidUntil"] < timezone.localdate().isoformat() or
                 row["redeemValidUntil"] != snapshot.get("redeemValidUntil"))):
            raise OrderError("核销有效期已变化，请重新报价。", "REDEEM_VALIDITY_CHANGED", 409)
        if row["priceFen"] != snapshot["unitPriceFen"]:
            raise OrderError("价格已变化，请重新报价。", "PRICE_CHANGED", 409)
        quantity = snapshot["quantity"]
        amount = row["priceFen"] * quantity
        if (type(quantity) is not int or not 1 <= quantity <= 9999 or
                snapshot["baseQuantity"] != quantity * unit.ratio or
                snapshot["lineAmountFen"] != amount):
            raise OrderError("报价明细不一致，请重新报价。", "QUOTE_CHANGED", 409)
        result.append((sku, unit, row, quantity, amount))
    if sum(item[4] for item in result) != quote.goods_total_fen:
        raise OrderError("报价金额不一致，请重新报价。", "QUOTE_CHANGED", 409)
    return result


def submit_order(member, body, key):
    quote_id, method = _request(body)
    if not isinstance(key, UUID):
        raise OrderError("请提供 UUID 格式的防重复请求标识。")
    with transaction.atomic():
        _key_lock(member.id, key)
        member = Member.objects.select_related("grade").select_for_update(of=("self",)).filter(
            pk=member.id, enabled=True).first()
        if member is None:
            raise OrderError("登录已失效，请重新登录。", "SESSION_EXPIRED", 401)
        quote = CheckoutQuote.objects.select_for_update().filter(pk=quote_id,
                                                                  member_id=member.id).first()
        if not quote:
            raise OrderError("报价不存在或不可访问。", "QUOTE_NOT_FOUND", 404)
        digest = _business_digest(quote, method)
        prior = OrderIdempotency.objects.select_related("order").filter(
            member=member, scope="CREATE_ORDER", key=key).first()
        if prior:
            if timezone.now() - prior.created_at >= timedelta(hours=24):
                raise OrderError("防重复标识已过期，请查询原订单。", "IDEMPOTENCY_KEY_EXPIRED", 409,
                                 [{"orderId": str(prior.order_id)}])
            if prior.request_digest != digest:
                raise OrderError("同一防重复标识对应不同订单内容。", "IDEMPOTENCY_CONFLICT", 409)
            return order_data(prior.order, include_voucher_code=True), True
        if not payment_method_enabled(method):
            raise OrderError("该支付方式的收款确认尚未开放，暂不能提交订单。", "SETTLEMENT_NOT_READY", 503)
        if quote.expires_at <= timezone.now():
            raise OrderError("报价已过期，请重新核对。", "QUOTE_EXPIRED", 409)
        try:
            policy = current_shipping_policy(lock=True)
            shipping_fee = shipping_fee_for_lines(quote.lines, policy)
        except ShippingUnavailable as exc:
            raise OrderError(str(exc), "SHIPPING_UNAVAILABLE", 503) from exc
        if (policy.revision != quote.shipping_policy_revision or
                shipping_fee != quote.shipping_fee_fen):
            raise OrderError("运费已变化，请重新报价。", "SHIPPING_CHANGED", 409)
        from benefits.policy import current_policy
        benefit_policy = current_policy(lock=True)
        if quote.benefit_policy_snapshot != benefit_policy:
            raise OrderError("等级或积分规则已变化，请重新报价。", "BENEFIT_POLICY_CHANGED", 409)
        warehouse = Warehouse.objects.filter(is_default=True, enabled=True).first()
        if not warehouse:
            raise OrderError("默认仓不可用。", "WAREHOUSE_UNAVAILABLE", 409)
        needs_shipping = any(line.get("fulfillmentKind") == "SHIP" for line in quote.lines)
        address = _address_snapshot(quote, member, needs_shipping)
        current = _current_lines(quote, member)
        # Recheck available stock only after the balances are locked in a
        # fixed order; a quote is never a reservation.
        from inventory.reservations import lock_default_balances
        balance_ids = [UUID(line["skuId"]) for line in quote.lines]
        locked_balances = lock_default_balances(warehouse.id, balance_ids)
        for sku, unit, row, quantity, amount in current:
            balance = locked_balances.get(sku.id)
            if not balance or balance.on_hand_base_units - balance.reserved_base_units < quantity * unit.ratio:
                raise OrderError("商品库存不足，请重新报价。", "OUT_OF_STOCK", 409)
        total = sum(item[4] for item in current)
        if (quote.payable_fen != total + shipping_fee - quote.coupon_discount_fen
                - quote.points_discount_fen or
                quote.points_discount_fen != quote.points_to_use * benefit_policy["deductFen"] // benefit_policy["deductPoints"] or
                not isinstance(quote.allocations, list) or
                len(quote.allocations) != len(current)):
            raise OrderError("结算金额不一致，请重新报价。", "QUOTE_CHANGED", 409)
        for index, (_, _, _, _, amount) in enumerate(current):
            allocation = quote.allocations[index]
            if (not isinstance(allocation, dict) or
                    set(allocation) != {"couponDiscountFen", "pointsDiscountFen", "payableFen"} or
                    any(type(allocation[key]) is not int or allocation[key] < 0 for key in allocation) or
                    allocation["payableFen"] != amount - allocation["couponDiscountFen"]
                    - allocation["pointsDiscountFen"]):
                raise OrderError("优惠分摊不一致，请重新报价。", "QUOTE_CHANGED", 409)
        if (sum(value["couponDiscountFen"] for value in quote.allocations) != quote.coupon_discount_fen or
                sum(value["pointsDiscountFen"] for value in quote.allocations) != quote.points_discount_fen):
            raise OrderError("优惠分摊不一致，请重新报价。", "QUOTE_CHANGED", 409)
        from payments.policy import order_payment_snapshot
        from payments.service import PaymentError
        try:
            payment_snapshot = order_payment_snapshot(method)
        except PaymentError as exc:
            raise OrderError(str(exc), exc.code, exc.status) from exc
        order = Order.objects.create(
            payment_instructions=payment_snapshot,
            order_no=f"O{uuid4().hex.upper()}", member=member, quote_id=quote_id,
            payment_method=method, address_snapshot=address,
            goods_total_fen=total, shipping_fee_fen=shipping_fee,
            shipping_policy_revision=policy.revision, coupon_id=quote.coupon_id,
            coupon_discount_fen=quote.coupon_discount_fen,
            benefit_policy_snapshot=benefit_policy,
            points_to_use=quote.points_to_use, points_discount_fen=quote.points_discount_fen,
            payable_fen=quote.payable_fen,
            expires_at=timezone.now() + timedelta(minutes=payment_snapshot["timeoutMinutes"]))
        from fulfillment.service import snapshot_order_policy_locked
        snapshot_order_policy_locked(order)
        from aftersales.service import snapshot_order_policy_locked as snapshot_aftersale_policy
        snapshot_aftersale_policy(order)
        lines = []
        for index, (sku, unit, row, quantity, amount) in enumerate(current):
            allocation = quote.allocations[index]
            lines.append(OrderLine.objects.create(
                order=order, product_id=sku.product_id, sku=sku, warehouse=warehouse,
                unit_version=unit, product_name=row["name"], sku_code=sku.sku_code,
                specs_snapshot=row["specs"], fulfillment_kind=row["fulfillmentKind"],
                redeem_valid_until=row["redeemValidUntil"],
                base_unit=unit.base_unit, sale_unit=unit.sale_unit, ratio=unit.ratio,
                quantity=quantity, base_quantity=quantity * unit.ratio,
                unit_price_fen=row["priceFen"], goods_amount_fen=amount,
                coupon_discount_fen=allocation["couponDiscountFen"],
                points_discount_fen=allocation["pointsDiscountFen"],
                payable_fen=allocation["payableFen"]))
        try:
            reserve_order_lines(lines, locked_balances)
        except ReservationError as exc:
            raise OrderError(str(exc), exc.code, 409) from exc
        benefit_lines = [{"skuId": str(sku.id), "productId": str(sku.product_id),
                          "fulfillmentKind": row["fulfillmentKind"], "amountFen": amount}
                         for sku, _, row, _, amount in current]
        try:
            benefits = reserve_benefits(member, order.id, benefit_lines,
                                        quote.coupon_id, quote.points_to_use, policy=benefit_policy)
        except BenefitError as exc:
            raise OrderError(str(exc), exc.code, exc.status) from exc
        if (benefits["couponDiscountFen"] != quote.coupon_discount_fen or
                benefits["pointsDiscountFen"] != quote.points_discount_fen or
                benefits["allocations"] != quote.allocations):
            raise OrderError("优惠权益已变化，请重新报价。", "BENEFIT_CHANGED", 409)
        from benefits.lifecycle import snapshot_order_benefits_locked
        snapshot_order_benefits_locked(order)
        if order.payable_fen == 0:
            settle_paid_order_locked(order)
        OrderIdempotency.objects.create(member=member, key=key, request_digest=digest, order=order)
        return order_data(order, include_voucher_code=True), False


def settle_paid_order_locked(order):
    """One order-owned transition for zero-value and verified paid orders."""
    if not connection.in_atomic_block:
        raise RuntimeError("确认收款必须在数据库事务内执行。")
    if order.status != Order.Status.PENDING_PAYMENT:
        raise OrderError("订单当前状态不可确认收款。", "ORDER_NOT_PAYABLE", 409)
    from customers.consumption import lock_consumption_member
    lock_consumption_member(order.member_id)
    assert_order_reservations(order)
    assert_order_benefits(order.id, order.member_id, order.coupon_id,
                          order.coupon_discount_fen, order.points_to_use)
    consume_order_reservations(order)
    consume_benefits(order.id)
    order.status = Order.Status.PAID
    order.paid_at = timezone.now()
    order.revision += 1
    order.save(update_fields=["status", "paid_at", "revision"])
    from fulfillment.service import issue_paid_vouchers_locked
    issue_paid_vouchers_locked(order)


def _close_locked(order, reason):
    if order.status == Order.Status.CLOSED:
        return False
    if order.status != Order.Status.PENDING_PAYMENT:
        raise OrderError("已付款订单不能取消。", "ORDER_NOT_CANCELLABLE", 409)
    from customers.consumption import lock_consumption_member
    lock_consumption_member(order.member_id)
    release_order_reservations(order)
    release_benefits(order.id)
    order.status = Order.Status.CLOSED
    order.closed_at = timezone.now()
    order.close_reason = reason
    order.revision += 1
    order.save(update_fields=["status", "closed_at", "close_reason", "revision"])
    from payments.wechat_intents import mark_close_requested
    mark_close_requested(order.id)
    return True


def close_expired_order_locked(order):
    if order.status == Order.Status.PENDING_PAYMENT and order.expires_at <= timezone.now():
        return _close_locked(order, "TIMEOUT")
    return False


def cancel_order(member, order_id):
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id, member_id=member.id).first()
        if not order:
            raise OrderError("订单不存在。", "ORDER_NOT_FOUND", 404)
        _close_locked(order, "TIMEOUT" if order.expires_at <= timezone.now() else "CUSTOMER")
        return order_data(order, include_voucher_code=True)


def close_expired_orders(batch_size=100):
    count = 0
    ids = list(Order.objects.filter(status=Order.Status.PENDING_PAYMENT,
                                    expires_at__lte=timezone.now()).order_by("expires_at")
               .values_list("id", flat=True)[:batch_size])
    for order_id in ids:
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if order and close_expired_order_locked(order):
                count += 1
    return count


def close_wechat_order(order_id):
    """Provider verified CLOSED/REVOKED: release through the order domain."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.payment_method == "WECHAT" and order.status == Order.Status.PENDING_PAYMENT:
            _close_locked(order, "PAYMENT_CLOSED")
