"""Coupon and points pricing, reservation, release, and consumption.

The caller owns the surrounding order transaction. The revisioned policy converts points to integer fen; shipping is deliberately outside this module's line amounts.
"""

from datetime import datetime
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (CouponCampaign, CouponEvent, MemberCoupon, PointsAccount, PointsEvent,
                     PointsGrant, PointsReservation)


MAX_LINES = 50
MAX_AMOUNT_FEN = 9_000_000_000_000
POINTS_MAX_PERCENT = 20


class BenefitError(ValueError):
    def __init__(self, message, code="BENEFIT_INVALID", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _uuid(value, label):
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    raise BenefitError(f"{label}格式不正确。")


def _lines(lines):
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        raise BenefitError("结算商品数量不正确。")
    normalized, sku_ids, total = [], set(), 0
    for line in lines:
        if not isinstance(line, dict) or not {"productId", "skuId", "fulfillmentKind", "amountFen"} <= set(line):
            raise BenefitError("结算商品格式不正确。")
        product_id = _uuid(line["productId"], "商品")
        sku_id = _uuid(line["skuId"], "SKU")
        amount = line["amountFen"]
        if (sku_id in sku_ids or line["fulfillmentKind"] not in {"SHIP", "REDEEM"} or
                type(amount) is not int or not 0 <= amount <= MAX_AMOUNT_FEN):
            raise BenefitError("结算商品金额、履约类型或 SKU 不正确。")
        sku_ids.add(sku_id)
        total += amount
        if total > MAX_AMOUNT_FEN:
            raise BenefitError("结算商品总额超过上限。")
        normalized.append({"productId": str(product_id), "skuId": str(sku_id),
                           "fulfillmentKind": line["fulfillmentKind"], "amountFen": amount})
    return normalized


def _inputs(member, lines, coupon_id, points_to_use, at):
    rows = _lines(lines)
    if type(points_to_use) is not int or points_to_use < 0:
        raise BenefitError("积分抵扣数量须为非负整数。")
    selected_id = _uuid(coupon_id, "优惠券") if coupon_id is not None else None
    if member is None and (selected_id or points_to_use):
        raise BenefitError("请先登录再选择优惠券或积分。", "LOGIN_REQUIRED")
    if at is None:
        at = timezone.now()
    if not isinstance(at, datetime) or not timezone.is_aware(at):
        raise BenefitError("结算时间格式不正确。")
    return rows, selected_id, at


def _eligible_indices(campaign, rows):
    product_ids = campaign.product_ids
    if not isinstance(product_ids, list) or any(not isinstance(value, str) for value in product_ids):
        return []  # Malformed scope fails closed.
    allowed = set(product_ids)
    return [index for index, row in enumerate(rows)
            if (not allowed or row["productId"] in allowed) and
            (row["fulfillmentKind"] != "REDEEM" or campaign.redeem_eligible)]


def _coupon_discount(coupon, rows, at):
    campaign = coupon.campaign
    if (coupon.status != MemberCoupon.Status.AVAILABLE or not campaign.active or
            not campaign.valid_from <= at < campaign.valid_until or
            (coupon.restored_valid_until is not None and coupon.restored_valid_until <= at)):
        return 0, []
    indices = _eligible_indices(campaign, rows)
    eligible = sum(rows[index]["amountFen"] for index in indices)
    if (eligible <= 0 or campaign.kind not in CouponCampaign.Kind.values or
            (campaign.kind == CouponCampaign.Kind.FULL_REDUCTION and eligible < campaign.min_goods_fen)):
        return 0, []
    return min(campaign.discount_fen, eligible), indices


def _selected_coupon(member, coupon_id, rows, at, *, lock=False):
    if coupon_id is None:
        return None, 0, []
    coupons = MemberCoupon.objects.select_related("campaign")
    if lock:
        coupons = coupons.select_for_update(of=("self",))
    coupon = coupons.filter(pk=coupon_id, member_id=member.id).first()
    if coupon is None:
        raise BenefitError("优惠券不可用，请重新结算。", "COUPON_UNAVAILABLE", 409)
    discount, indices = _coupon_discount(coupon, rows, at)
    if discount == 0:
        raise BenefitError("优惠券不可用，请重新结算。", "COUPON_UNAVAILABLE", 409)
    return coupon, discount, indices


def _available_coupons(member, rows, at):
    if member is None:
        return []
    queryset = (MemberCoupon.objects.select_related("campaign")
                .filter(member=member, status=MemberCoupon.Status.AVAILABLE,
                        campaign__active=True, campaign__valid_from__lte=at,
                        campaign__valid_until__gt=at)
                .filter(Q(restored_valid_until__isnull=True) | Q(restored_valid_until__gt=at))
                .order_by("campaign__valid_until", "id")[:100])
    return [{"id": str(coupon.id), "title": coupon.campaign.title, "discountFen": discount}
            for coupon in queryset
            if (discount := _coupon_discount(coupon, rows, at)[0]) > 0]


def _available_points(member, at):
    if member is None:
        return 0
    account = PointsAccount.objects.filter(member=member).values_list(
        "settled_points", "frozen_points").first()
    if account is None:
        return 0
    expired_available = (PointsGrant.objects.filter(member=member, expires_at__lte=at)
                         .aggregate(total=Sum("available_points"))["total"] or 0)
    account_available = max(0, account[0] - account[1] - expired_available)
    if account_available == 0:
        return 0
    lot_available = (PointsGrant.objects.filter(member=member, expires_at__gt=at,
                                                available_points__gt=0)
                     .aggregate(total=Sum("available_points"))["total"] or 0)
    return min(account_available, lot_available)


def _lock_account(member):
    PointsAccount.objects.get_or_create(member=member)
    return PointsAccount.objects.select_for_update().get(member=member)


def _allocate(amount, weights):
    total = sum(weights)
    result = [0] * len(weights)
    if not amount:
        return result
    if total <= 0 or amount > total:
        raise BenefitError("抵扣金额超过可用商品金额。")
    quotients = [divmod(amount * weight, total) for weight in weights]
    result = [quotient for quotient, _ in quotients]
    for index in sorted(range(len(weights)), key=lambda i: (-quotients[i][1], i))[:amount - sum(result)]:
        result[index] += 1
    return result


def _settlement(rows, coupon_discount, eligible_indices, available_points,
                points_to_use, coupon_id, *, changed_status=400, policy=None):
    weights = [row["amountFen"] if index in eligible_indices else 0
               for index, row in enumerate(rows)]
    coupon_allocations = _allocate(coupon_discount, weights)
    post_coupon = [row["amountFen"] - coupon_allocations[index]
                   for index, row in enumerate(rows)]
    from .policy import current_policy
    policy = policy or current_policy()
    discount_fen = points_to_use * policy["deductFen"] // policy["deductPoints"]
    if points_to_use and discount_fen == 0:
        raise BenefitError("所选积分不足以抵扣 1 分钱。", "POINTS_TOO_SMALL", changed_status)
    if discount_fen > sum(post_coupon) * policy["maxPercent"] // 100:
        raise BenefitError("积分抵扣超过当前配置的单笔上限。", "POINTS_LIMIT_EXCEEDED", changed_status)
    if points_to_use > available_points:
        raise BenefitError("可用积分已变化，请重新结算。", "POINTS_UNAVAILABLE", changed_status)
    points_allocations = _allocate(discount_fen, post_coupon)
    allocations = [{"couponDiscountFen": coupon_allocations[index],
                    "pointsDiscountFen": points_allocations[index],
                    "payableFen": post_coupon[index] - points_allocations[index]}
                   for index in range(len(rows))]
    return {"couponDiscountFen": coupon_discount, "pointsDiscountFen": discount_fen,
            "benefitPolicySnapshot": policy,
            "availablePoints": available_points, "allocations": allocations,
            "selectedCouponId": str(coupon_id) if coupon_id else None,
            "pointsToUse": points_to_use}


def quote_benefits(member, lines, coupon_id=None, points_to_use=0, *, at=None):
    rows, selected_id, at = _inputs(member, lines, coupon_id, points_to_use, at)
    _, discount, indices = _selected_coupon(member, selected_id, rows, at)
    available = _available_points(member, at)
    result = _settlement(rows, discount, indices, available, points_to_use, selected_id)
    result["availableCoupons"] = _available_coupons(member, rows, at)
    return result


def reserve_benefits(member, order_id, lines, coupon_id=None, points_to_use=0, *, at=None, policy=None):
    """Reprice and reserve inside the caller's order transaction."""
    if not connection.in_atomic_block:
        raise BenefitError("权益占用必须与订单处于同一事务。", "TRANSACTION_REQUIRED", 500)
    order_id = _uuid(order_id, "订单")
    if member is None:
        raise BenefitError("请先登录再提交订单。", "LOGIN_REQUIRED")
    rows, selected_id, at = _inputs(member, lines, coupon_id, points_to_use, at)
    if (MemberCoupon.objects.filter(reserved_order_id=order_id).exists() or
            PointsReservation.objects.filter(order_id=order_id).exists()):
        raise BenefitError("订单权益已经处理。", "BENEFIT_ALREADY_PROCESSED", 409)
    from customers.consumption import lock_consumption_member

    lock_consumption_member(member.id)
    coupon, discount, indices = _selected_coupon(member, selected_id, rows, at, lock=True)
    # One lock order: coupon, then member account, then expiring point lots.
    account = _lock_account(member) if points_to_use else None
    grants = (list(PointsGrant.objects.select_for_update().filter(
        member=member, available_points__gt=0).order_by("expires_at", "id"))
        if points_to_use else [])
    if account:
        from .lifecycle import normalize_resolved_lots_locked

        normalize_resolved_lots_locked(account, grants, at)
        grants = [grant for grant in grants if grant.expires_at > at and grant.available_points > 0]
    available = (min(sum(grant.available_points for grant in grants),
                     max(0, account.settled_points - account.frozen_points))
                 if account else _available_points(member, at))
    result = _settlement(rows, discount, indices, available, points_to_use,
                         selected_id, changed_status=409, policy=policy)
    result["availableCoupons"] = _available_coupons(member, rows, at)
    if coupon:
        coupon.status = MemberCoupon.Status.RESERVED
        coupon.reserved_order_id = order_id
        coupon.reserved_at = at
        coupon.save(update_fields=["status", "reserved_order_id", "reserved_at"])
        CouponEvent.objects.create(coupon=coupon, member=member, order_id=order_id,
                                   kind=CouponEvent.Kind.RESERVE, discount_fen=discount)
    remaining = points_to_use
    for grant in grants:
        if remaining == 0:
            break
        amount = min(remaining, grant.available_points)
        grant.available_points -= amount
        grant.reserved_points += amount
        grant.save(update_fields=["available_points", "reserved_points"])
        PointsReservation.objects.create(member=member, grant=grant, order_id=order_id, amount=amount)
        PointsEvent.objects.create(member=member, grant=grant, order_id=order_id,
                                   kind=PointsEvent.Kind.RESERVE, amount=amount)
        remaining -= amount
    if account and points_to_use:
        account.frozen_points += points_to_use
        account.save(update_fields=["frozen_points", "updated_at"])
    return result


def _resolve(order_id, status):
    if not connection.in_atomic_block:
        raise BenefitError("权益变更必须与订单处于同一事务。", "TRANSACTION_REQUIRED", 500)
    order_id = _uuid(order_id, "订单")
    with transaction.atomic():
        from customers.consumption import lock_consumption_member

        all_member_ids = set(MemberCoupon.objects.filter(reserved_order_id=order_id)
                             .values_list("member_id", flat=True))
        all_member_ids.update(PointsReservation.objects.filter(order_id=order_id)
                              .values_list("member_id", flat=True))
        for member_id in sorted(all_member_ids):
            lock_consumption_member(member_id)
        coupons = list(MemberCoupon.objects.select_for_update().filter(
            reserved_order_id=order_id, status=MemberCoupon.Status.RESERVED))
        member_ids = sorted(set(PointsReservation.objects.filter(
            order_id=order_id, status=PointsReservation.Status.RESERVED)
            .values_list("member_id", flat=True)))
        accounts = {account.member_id: account for account in PointsAccount.objects.select_for_update()
                    .filter(member_id__in=member_ids).order_by("member_id")}
        if len(accounts) != len(member_ids):
            raise BenefitError("积分账户缺失，无法结算订单。", "POINTS_ACCOUNT_MISSING", 500)
        reservations = list(PointsReservation.objects.select_for_update().filter(
            order_id=order_id, status=PointsReservation.Status.RESERVED).order_by("grant_id"))
        grant_ids = [row.grant_id for row in reservations]
        grants = {grant.id: grant for grant in PointsGrant.objects.select_for_update().filter(
            id__in=grant_ids).order_by("expires_at", "id")}
        now = timezone.now()
        for coupon in coupons:
            reserved_discount = CouponEvent.objects.get(
                coupon=coupon, order_id=order_id, kind=CouponEvent.Kind.RESERVE).discount_fen
            if status == PointsReservation.Status.RELEASED:
                coupon.status = MemberCoupon.Status.AVAILABLE
                coupon.reserved_order_id = None
                coupon.reserved_at = None
                coupon.save(update_fields=["status", "reserved_order_id", "reserved_at"])
            else:
                coupon.status = MemberCoupon.Status.USED
                coupon.used_at = now
                coupon.save(update_fields=["status", "used_at"])
            CouponEvent.objects.create(
                coupon=coupon, member=coupon.member, order_id=order_id,
                kind=CouponEvent.Kind.RELEASE if status == PointsReservation.Status.RELEASED
                else CouponEvent.Kind.CONSUME, discount_fen=reserved_discount)
        for reservation in reservations:
            grant = grants[reservation.grant_id]
            grant.reserved_points -= reservation.amount
            if status == PointsReservation.Status.RELEASED:
                grant.available_points += reservation.amount
            else:
                grant.consumed_points += reservation.amount
            grant.save(update_fields=["available_points", "reserved_points", "consumed_points"])
            reservation.status = status
            reservation.resolved_at = now
            reservation.save(update_fields=["status", "resolved_at"])
            PointsEvent.objects.create(member=reservation.member, grant=grant, order_id=order_id,
                                       kind=PointsEvent.Kind.RELEASE if status == PointsReservation.Status.RELEASED
                                       else PointsEvent.Kind.CONSUME, amount=reservation.amount)
        for member_id, account in accounts.items():
            total = sum(row.amount for row in reservations if row.member_id == member_id)
            if total > account.frozen_points:
                raise BenefitError("积分冻结余额不一致。", "POINTS_ACCOUNT_MISMATCH", 500)
            account.frozen_points -= total
            if status == PointsReservation.Status.CONSUMED:
                account.settled_points -= total
            account.save(update_fields=["frozen_points", "settled_points", "updated_at"])
        if status == PointsReservation.Status.RELEASED:
            from .lifecycle import normalize_resolved_lots_locked

            for member_id, account in accounts.items():
                normalize_resolved_lots_locked(account,
                    [grant for grant in grants.values() if grant.member_id == member_id], now)
        return bool(coupons or reservations)


def release_benefits(order_id):
    """Release unpaid entitlements once; expired lots remain unavailable."""
    return _resolve(order_id, PointsReservation.Status.RELEASED)


def consume_benefits(order_id):
    """Finalize a paid order once; payment confirmation calls this atomically."""
    return _resolve(order_id, PointsReservation.Status.CONSUMED)


def assert_order_benefits(order_id, member_id, coupon_id, coupon_discount_fen, points_to_use):
    """Reject a paid transition if the held entitlements no longer match its snapshot."""
    if not connection.in_atomic_block:
        raise BenefitError("核对订单权益必须在同一事务。", "TRANSACTION_REQUIRED", 500)
    coupons = list(MemberCoupon.objects.filter(reserved_order_id=order_id,
                                                status=MemberCoupon.Status.RESERVED))
    if coupon_discount_fen:
        if (len(coupons) != 1 or coupons[0].id != coupon_id or coupons[0].member_id != member_id or
                not CouponEvent.objects.filter(coupon=coupons[0], order_id=order_id,
                                               member_id=member_id,
                                               kind=CouponEvent.Kind.RESERVE,
                                               discount_fen=coupon_discount_fen).exists()):
            raise BenefitError("订单优惠券占用与成交快照不一致。", "BENEFIT_HOLD_MISMATCH", 500)
    elif coupons:
        raise BenefitError("订单优惠券占用与成交快照不一致。", "BENEFIT_HOLD_MISMATCH", 500)
    points = PointsReservation.objects.filter(order_id=order_id,
                                               status=PointsReservation.Status.RESERVED)
    if (points.exclude(member_id=member_id).exists() or
            points.exclude(grant__member_id=member_id).exists() or
            (points.aggregate(total=Sum("amount"))["total"] or 0) != points_to_use):
        raise BenefitError("订单积分冻结与成交快照不一致。", "BENEFIT_HOLD_MISMATCH", 500)


def grant_points(member, points, expires_at, source_ref):
    """Idempotent trusted issuance primitive; no public issuance endpoint."""
    if (member is None or type(points) is not int or points <= 0 or
            not isinstance(source_ref, str) or not 1 <= len(source_ref) <= 120 or
            not isinstance(expires_at, datetime) or not timezone.is_aware(expires_at)):
        raise BenefitError("积分发放数据不正确。")
    with transaction.atomic():
        from customers.consumption import lock_consumption_member

        lock_consumption_member(member.id)
        account = _lock_account(member)
        from .lifecycle import normalize_resolved_lots_locked

        existing_lots = list(PointsGrant.objects.select_for_update().filter(member=member, available_points__gt=0)
                             .order_by("expires_at", "id"))
        normalize_resolved_lots_locked(account, existing_lots, timezone.now())
        debt_offset = min(points, max(0, -account.settled_points))
        grant, created = PointsGrant.objects.get_or_create(
            source_ref=source_ref,
            defaults={"member": member, "original_points": points,
                      "available_points": points - debt_offset,
                      "consumed_points": debt_offset, "expires_at": expires_at})
        if (grant.member_id != member.id or grant.original_points != points or
                grant.expires_at != expires_at):
            raise BenefitError("积分发放来源已用于其他内容。", "POINTS_SOURCE_CONFLICT", 409)
        if created:
            account.settled_points += points
            account.save(update_fields=["settled_points", "updated_at"])
            PointsEvent.objects.create(member=member, grant=grant, kind=PointsEvent.Kind.GRANT,
                                       amount=points)
        return grant


def reserve_exchange_points(member,order_id,points,*,at=None):
    """Reserve exact integer points for a pure points order; no cash conversion."""
    if not connection.in_atomic_block:
        raise BenefitError('积分兑换占用须与订单处于同一事务。','TRANSACTION_REQUIRED',500)
    if type(points) is not int or not 1<=points<=99000000:
        raise BenefitError('兑换积分须为范围内的正整数。')
    order_id=_uuid(order_id,'订单'); at=at or timezone.now()
    from customers.consumption import lock_consumption_member
    lock_consumption_member(member.id)
    if PointsReservation.objects.filter(order_id=order_id).exists():
        raise BenefitError('兑换积分已处理。','BENEFIT_ALREADY_PROCESSED',409)
    account=_lock_account(member)
    grants=list(PointsGrant.objects.select_for_update().filter(member=member,available_points__gt=0).order_by('expires_at','id'))
    from .lifecycle import normalize_resolved_lots_locked
    normalize_resolved_lots_locked(account,grants,at)
    grants=[grant for grant in grants if grant.expires_at>at and grant.available_points>0]
    available=min(sum(grant.available_points for grant in grants),max(0,account.settled_points-account.frozen_points))
    if points>available: raise BenefitError('可用积分不足，请重新核对。','POINTS_UNAVAILABLE',409)
    remaining=points
    for grant in grants:
        if not remaining: break
        amount=min(remaining,grant.available_points)
        grant.available_points-=amount;grant.reserved_points+=amount
        grant.save(update_fields=['available_points','reserved_points'])
        PointsReservation.objects.create(member=member,grant=grant,order_id=order_id,amount=amount)
        PointsEvent.objects.create(member=member,grant=grant,order_id=order_id,kind='RESERVE',amount=amount)
        remaining-=amount
    account.frozen_points+=points;account.save(update_fields=['frozen_points','updated_at'])


def available_exchange_points(member,at=None):
    """Read-only available points boundary for independently priced exchange."""
    return _available_points(member,at or timezone.now())
