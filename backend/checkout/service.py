"""Server-owned pricing and availability for selection/quote flows."""

from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from catalog.quote_access import quote_catalog_rows
from benefits.service import BenefitError, quote_benefits
from inventory.availability import default_available_base_units
from inventory.pool_access import resolve_anchor_ids
from payments.availability import enabled_payment_methods
from shipping.service import current_shipping_policy, shipping_fee_for_lines

from .models import CheckoutQuote


MAX_LINES = 50
MAX_QUANTITY = 9999
MAX_TOTAL_FEN = 9_000_000_000_000


class QuoteValidationError(ValueError):
    pass


def _uuid(value):
    if not isinstance(value, str):
        raise QuoteValidationError("SKU 编号格式不正确。")
    try:
        return UUID(value)
    except ValueError as exc:
        raise QuoteValidationError("SKU 编号格式不正确。") from exc


def _input_lines(body):
    if set(body) - {"items", "addressId", "couponId", "pointsToUse"}:
        raise QuoteValidationError("报价字段不正确。")
    items = body.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_LINES:
        raise QuoteValidationError("请选择 1 至 50 个 SKU。")
    normalized, ids = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"skuId", "quantity", "seenPriceFen"}:
            raise QuoteValidationError("商品行格式不正确。")
        sku_id = _uuid(item.get("skuId"))
        quantity = item.get("quantity")
        seen = item.get("seenPriceFen")
        if (sku_id in ids or type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY or
                (seen is not None and (type(seen) is not int or seen < 0))):
            raise QuoteValidationError("SKU 不可重复，数量须为 1 至 9999 的整数。")
        ids.add(sku_id)
        normalized.append((sku_id, quantity, seen))
    return normalized


def _benefit_selection(body, member):
    coupon_id = body.get("couponId")
    if coupon_id is not None:
        if not isinstance(coupon_id, str):
            raise QuoteValidationError("优惠券 ID 格式不正确。")
        try:
            coupon_id = UUID(coupon_id)
        except ValueError as exc:
            raise QuoteValidationError("优惠券 ID 格式不正确。") from exc
    points = body.get("pointsToUse", 0)
    if type(points) is not int or points < 0:
        raise QuoteValidationError("使用积分须为非负整数。")
    if (coupon_id or points) and member is None:
        raise QuoteValidationError("请登录后选择优惠券或积分。")
    return coupon_id, points


def _address_id(body, member):
    raw = body.get("addressId")
    if raw is None:
        return None
    if not member:
        raise QuoteValidationError("请登录后选择收货地址。")
    address_id = _uuid(raw)
    from customers.addresses import member_owns_active_address
    if not member_owns_active_address(member, address_id):
        raise QuoteValidationError("收货地址已失效，请重新选择。")
    return address_id


def create_quote(body, member, source_digest=""):
    requested = _input_lines(body)
    coupon_id, points_to_use = _benefit_selection(body, member)
    address_id = _address_id(body, member)
    ids = [item[0] for item in requested]
    skus = quote_catalog_rows(ids, member)
    warehouse, balances = default_available_base_units(ids)
    anchors = resolve_anchor_ids(ids)
    pool_demand = {}
    pool_available = {}
    for sku_id, quantity, _ in requested:
        sku = skus.get(sku_id)
        if sku and sku["onSale"] and sku["ratio"]:
            anchor = anchors[sku_id]
            pool_demand[anchor] = pool_demand.get(anchor, 0) + quantity * sku["ratio"]
            pool_available[anchor] = balances.get(sku_id, 0)
    short_pools = {anchor for anchor, demand in pool_demand.items()
                   if demand > pool_available[anchor]}

    lines, goods_total, ready, changed = [], 0, True, False
    for sku_id, quantity, seen in requested:
        sku = skus.get(sku_id)
        if not sku:
            ready = False
            lines.append({"skuId": str(sku_id), "productId": None, "name": "商品已失效",
                          "imageUrl": None, "fulfillmentKind": None, "redeemValidUntil": None, "specs": [], "quantity": quantity,
                          "saleUnit": None, "ratio": None, "baseQuantity": None,
                          "availableQuantity": 0, "unitPriceFen": 0, "lineAmountFen": 0,
                          "priceSource": None, "priceChanged": False, "status": "NOT_FOUND"})
            continue
        available = balances.get(sku_id, 0)
        ratio = sku["ratio"]
        available_qty = available // ratio if ratio else 0
        price = sku["priceFen"]
        status = "OK"
        if not sku["onSale"]:
            status = "OFF_SALE"
        elif (sku["fulfillmentKind"] == "REDEEM" and
                (not sku["redeemValidUntil"] or sku["redeemValidUntil"] < timezone.localdate().isoformat())):
            status = "REDEEM_VALIDITY_UNAVAILABLE"
        elif not ratio:
            status = "UNIT_UNAVAILABLE"
        elif not warehouse:
            status = "WAREHOUSE_UNAVAILABLE"
        elif quantity > available_qty or anchors[sku_id] in short_pools:
            status = "OUT_OF_STOCK"
        line_changed = seen is not None and seen != price
        changed = changed or line_changed
        ready = ready and status == "OK"
        amount = price * quantity
        goods_total += amount
        if goods_total > MAX_TOTAL_FEN:
            raise QuoteValidationError("报价总额超过上限，请减少选购数量。")
        lines.append({
            "skuId": str(sku_id), "productId": sku["productId"], "name": sku["name"],
            "specs": sku["specs"], "imageUrl": sku["imageUrl"],
            "fulfillmentKind": sku["fulfillmentKind"], "quantity": quantity,
            "redeemValidUntil": sku["redeemValidUntil"],
            "saleUnit": sku["saleUnit"], "ratio": ratio,
            "baseQuantity": quantity * ratio if ratio else None,
            "availableQuantity": available_qty, "unitPriceFen": price, "lineAmountFen": amount,
            "priceSource": sku["priceSource"],
            "priceChanged": line_changed, "status": status,
        })
    policy = current_shipping_policy()
    shipping_fee = shipping_fee_for_lines(lines, policy)
    if ready:
        benefit_lines = sorted([{"skuId": line["skuId"], "productId": line["productId"],
                                 "fulfillmentKind": line["fulfillmentKind"],
                                 "amountFen": line["lineAmountFen"]} for line in lines],
                               key=lambda row: row["skuId"])
        try:
            benefits = quote_benefits(member, benefit_lines, coupon_id, points_to_use)
        except BenefitError as exc:
            raise QuoteValidationError(str(exc)) from exc
    else:
        if coupon_id or points_to_use:
            raise QuoteValidationError("商品状态已变化，请先调整商品后重新选择优惠。")
        benefits = {"couponDiscountFen": 0, "pointsDiscountFen": 0, "availablePoints": 0,
                    "availableCoupons": [], "selectedCouponId": None, "pointsToUse": 0,
                    "allocations": [{"couponDiscountFen": 0, "pointsDiscountFen": 0,
                                     "payableFen": line["lineAmountFen"]}
                                    for line in sorted(lines, key=lambda row: row["skuId"])]}
    from benefits.policy import current_policy
    benefit_policy = benefits.get("benefitPolicySnapshot") or current_policy()
    coupon_discount = benefits["couponDiscountFen"]
    points_discount = benefits["pointsDiscountFen"]
    payable = goods_total + shipping_fee - coupon_discount - points_discount
    if not 0 <= payable <= MAX_TOTAL_FEN:
        raise QuoteValidationError("结算总额超过上限，请减少选购数量。")
    expires = timezone.now() + timedelta(minutes=10)
    with transaction.atomic():
        quote = CheckoutQuote.objects.create(member_id=member.id if member else None,
                                             source_digest=source_digest,
                                             address_id=address_id, lines=lines,
                                             goods_total_fen=goods_total,
                                             shipping_fee_fen=shipping_fee,
                                             shipping_policy_revision=policy.revision,
                                             coupon_id=coupon_id,
                                             coupon_discount_fen=coupon_discount,
                                             benefit_policy_snapshot=benefit_policy,
                                             points_to_use=points_to_use,
                                             points_discount_fen=points_discount,
                                             allocations=benefits["allocations"],
                                             payable_fen=payable,
                                             ready=ready, expires_at=expires)
    needs_shipping = any(line["fulfillmentKind"] == "SHIP" for line in lines)
    available_methods = enabled_payment_methods()
    return {"quoteId": str(quote.id), "expiresAt": expires.isoformat(), "lines": lines,
            "goodsTotalFen": goods_total, "payableFen": payable,
            "shippingFeeFen": shipping_fee, "shippingFeePending": False,
            "shippingPolicyRevision": policy.revision,
            "couponDiscountFen": coupon_discount, "pointsDiscountFen": points_discount,
            "selectedCouponId": benefits["selectedCouponId"],
            "pointsToUse": benefits["pointsToUse"],
            "pointsPolicy": {key: benefit_policy[key] for key in ["revision", "deductPoints", "deductFen", "maxPercent"]},
            "availablePoints": benefits["availablePoints"],
            "availableCoupons": benefits["availableCoupons"],
            "ready": ready, "confirmRequired": changed,
            "addressRequired": needs_shipping and address_id is None,
            "availablePaymentMethods": available_methods,
            "orderSubmissionAvailable": bool(available_methods),
            "message": "报价仅供核对，不预留库存；可用支付方式以服务端结果为准。"}
