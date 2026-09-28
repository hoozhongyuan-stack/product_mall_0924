"""Order-owned benefit lifecycle. No order/refund/fulfillment table reads.

The caller holds the order lock and supplies authoritative cumulative facts.
Lock order: member, coupon, points account, lots, settlement projection.
"""
from datetime import timedelta
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from customers.consumption import (adjust_effective_spend_locked, lock_consumption_member,
                                   snapshot_grade_thresholds)
from .models import (BenefitLedger, CouponEvent, MemberCoupon, OrderBenefitSettlement,
                     OrderBenefitSnapshot, PointsAccount, PointsGrant, PointsPolicy,
                     PointsReservation)
from .service import BenefitError


def _atomic():
    if not connection.in_atomic_block:
        raise BenefitError('权益结算必须处于订单事务内。', 'TRANSACTION_REQUIRED', 500)


def snapshot_order_benefits_locked(order):
    """Capture once at submission; never backfill missing historical snapshots."""
    _atomic()
    existing = OrderBenefitSnapshot.objects.filter(order_id=order.id).first()
    if existing:
        return existing
    policy, _ = PointsPolicy.objects.get_or_create(pk=1)
    coupon = (MemberCoupon.objects.select_related('campaign').filter(pk=order.coupon_id,
              member_id=order.member_id).first() if order.coupon_id else None)
    from .service import _allocate
    rows = list(order.lines.order_by("sku_id"))
    # The points amount is distinct from its money equivalent under D4 rules.
    weights = [line.points_discount_fen for line in rows]
    total = sum(weights)
    allocations = [order.points_to_use * weight // total if total else 0 for weight in weights]
    remainders = [order.points_to_use * weight % total if total else 0 for weight in weights]
    for index in sorted(range(len(rows)), key=lambda i: (-remainders[i], i))[:order.points_to_use - sum(allocations)]:
        allocations[index] += 1
    if getattr(order,'order_kind','CASH') == 'POINTS':
        allocations=[line.points_total for line in rows]
    lines = [{'lineId': str(line.id), 'quantity': line.quantity,
              'payableFen': line.payable_fen, 'points': allocations[index]}
             for index, line in enumerate(rows)]
    captured = order.benefit_policy_snapshot or {}
    return OrderBenefitSnapshot.objects.create(order_id=order.id, member_id=order.member_id,
        policy_revision=captured.get("revision", policy.revision), earn_unit_fen=captured.get("earnUnitFen", policy.earn_unit_fen),
        earn_points=captured.get("earnPoints", policy.earn_points), valid_days=captured.get("validDays", policy.valid_days),
        refund_valid_days=captured.get("refundValidDays", policy.refund_valid_days),
        grade_thresholds=captured.get("gradeThresholds", snapshot_grade_thresholds()),
        line_snapshot=lines, coupon_id=order.coupon_id,
        coupon_valid_until=(min(coupon.campaign.valid_until, coupon.restored_valid_until)
                            if coupon and coupon.restored_valid_until else
                            coupon.campaign.valid_until if coupon else None))


def _ledger(account, kind, amount, source_ref, *, order_id=None, grant=None, cause_ref=None):
    return BenefitLedger.objects.create(member_id=account.member_id, kind=kind, amount=amount,
        balance=account.settled_points, source_ref=source_ref, order_id=order_id, grant=grant,
        cause_ref=cause_ref or source_ref)


def _lot_totals(grants):
    return {(row['grant_id'], row['kind']): -(row['total'] or 0)
            for row in BenefitLedger.objects.filter(grant_id__in=[grant.id for grant in grants],
                kind__in=['EXPIRE', 'CLAWBACK_LOT', 'CLAWBACK_MOVE'])
            .values('grant_id', 'kind').annotate(total=Sum('amount'))}


def normalize_resolved_lots_locked(account, grants, at):
    _atomic()
    totals = _lot_totals(grants)
    for grant in grants:
        clawed = totals.get((grant.id, 'CLAWBACK_LOT'), 0)
        moved = totals.get((grant.id, 'CLAWBACK_MOVE'), 0)
        remove = min(grant.available_points, max(0, clawed - moved))
        if remove:
            grant.available_points -= remove
            grant.consumed_points += remove
            grant.save(update_fields=['available_points', 'consumed_points'])
            _ledger(account, 'CLAWBACK_MOVE', -remove,
                    f'clawmove:{grant.id}:{moved + remove}', grant=grant)
    return _expire_locked(account, grants, at)


def _expire_locked(account, grants, at):
    totals = _lot_totals(grants)
    count = 0
    for grant in grants:
        if grant.expires_at > at or not grant.available_points:
            continue
        amount = grant.available_points
        grant.available_points = 0
        grant.consumed_points += amount
        grant.save(update_fields=['available_points', 'consumed_points'])
        account.settled_points -= amount
        account.save(update_fields=['settled_points', 'updated_at'])
        prior = totals.get((grant.id, 'EXPIRE'), 0)
        _ledger(account, 'EXPIRE', -amount, f'expire:{grant.id}:{prior + amount}', grant=grant)
        count += 1
    return count


def expire_points(member, *, at=None):
    """Expire available lots only; held points stay frozen until order resolution."""
    at = at or timezone.now()
    with transaction.atomic():
        lock_consumption_member(member.id)
        account = PointsAccount.objects.select_for_update().get(member=member)
        grants = list(PointsGrant.objects.select_for_update().filter(member=member)
                      .order_by('expires_at', 'id'))
        return normalize_resolved_lots_locked(account, grants, at)


def _refund_targets(snapshot, refunds):
    by_line = {row['lineId']: row for row in snapshot.line_snapshot}
    cumulative = {}
    for row in refunds:
        try:
            line_id = str(UUID(str(row['lineId'])))
            qty, amount = row['quantity'], row['amountFen']
        except (KeyError, TypeError, ValueError):
            raise BenefitError('退款权益摘要不正确。', 'BENEFIT_REFUND_INVALID', 500)
        line = by_line.get(line_id)
        if (line is None or line_id in cumulative or type(qty) is not int or
                type(amount) is not int or not 0 <= qty <= line['quantity'] or
                not 0 <= amount <= line['payableFen']):
            raise BenefitError('退款权益摘要超出成交快照。', 'BENEFIT_REFUND_INVALID', 500)
        cumulative[line_id] = {'lineId': line_id, 'quantity': qty, 'amountFen': amount}
    returned = 0
    full = True
    for line_id, line in by_line.items():
        refund = cumulative.get(line_id, {'quantity': 0, 'amountFen': 0})
        if line['payableFen']:
            returned += line['points'] * refund['amountFen'] // line['payableFen']
        else:
            returned += line['points'] * refund['quantity'] // line['quantity']
        full &= (refund['quantity'] == line['quantity'] and
                 refund['amountFen'] == line['payableFen'])
    return cumulative, returned, full


def _issue_locked(account, points, expires_at, source_ref, order_id, kind, cause_ref=None):
    from .service import grant_points
    grant = grant_points(account.member, points, expires_at, source_ref)
    account.refresh_from_db()
    _ledger(account, kind, points, source_ref, order_id=order_id, grant=grant, cause_ref=cause_ref)
    return grant


def _return_deductions(account, snapshot, target, previous, at, cause_ref):
    if target <= previous:
        return previous
    reservations = list(PointsReservation.objects.filter(order_id=snapshot.order_id,
                         status='CONSUMED').select_related('grant').order_by('grant__expires_at', 'grant_id'))
    total = sum(row.amount for row in reservations)
    if not total:
        return previous
    if target > total:
        raise BenefitError('退款抵扣积分超过原核销记录。', 'BENEFIT_REFUND_INVALID', 500)
    prefix = 0
    for index, row in enumerate(reservations):
        old = min(row.amount, max(0, previous - prefix))
        new = min(row.amount, max(0, target - prefix))
        prefix += row.amount
        if new == old:
            continue
        expiry = (row.grant.expires_at if row.grant.expires_at > at else
                  at + timedelta(days=snapshot.refund_valid_days))
        _issue_locked(account, new - old, expiry,
                      f'return:{snapshot.order_id}:{row.grant_id}:{new}',
                      snapshot.order_id, 'RETURN', cause_ref)
    return target


def _clawback(account, snapshot, amount, revision, cause_ref):
    grants = list(PointsGrant.objects.select_for_update().filter(member_id=snapshot.member_id,
        source_ref__startswith=f'earn:{snapshot.order_id}').order_by('expires_at', 'id'))
    remaining, debit = amount, 0
    totals = _lot_totals(grants)
    for grant in grants:
        if not remaining:
            break
        # Natural expiry is already deducted. Never create debt for that same loss.
        expired = totals.get((grant.id, 'EXPIRE'), 0)
        clawed = totals.get((grant.id, 'CLAWBACK_LOT'), 0)
        eligible = max(0, grant.original_points - expired - clawed)
        take = min(remaining, eligible)
        remove = min(grant.available_points, take)
        grant.available_points -= remove
        grant.consumed_points += remove
        grant.save(update_fields=['available_points', 'consumed_points'])
        if remove:
            moved = totals.get((grant.id, 'CLAWBACK_MOVE'), 0)
            _ledger(account, 'CLAWBACK_MOVE', -remove,
                    f'clawmove:{grant.id}:{moved + remove}', grant=grant)
        if take:
            _ledger(account, 'CLAWBACK_LOT', -take,
                    f'clawlot:{snapshot.order_id}:{revision}:{grant.id}',
                    order_id=snapshot.order_id, grant=grant, cause_ref=cause_ref)
        debit += take
        remaining -= take
    account.settled_points -= debit
    account.save(update_fields=['settled_points', 'updated_at'])
    _ledger(account, 'CLAWBACK', -debit, f'claw:{snapshot.order_id}:{revision}',
            order_id=snapshot.order_id, cause_ref=cause_ref)


def _restore_coupon(account, snapshot, settlement, full, at, cause_ref):
    if (not full or settlement.coupon_restored or not snapshot.coupon_id or
            not snapshot.coupon_valid_until or snapshot.coupon_valid_until <= at):
        return
    coupon = MemberCoupon.objects.select_for_update().filter(pk=snapshot.coupon_id,
              member_id=snapshot.member_id, reserved_order_id=snapshot.order_id, status='USED').first()
    if coupon is None:
        return
    coupon.status = 'AVAILABLE'
    coupon.reserved_order_id = None
    coupon.reserved_at = None
    coupon.used_at = None
    coupon.restored_valid_until = snapshot.coupon_valid_until
    coupon.save(update_fields=['status', 'reserved_order_id', 'reserved_at', 'used_at',
                              'restored_valid_until'])
    discount = CouponEvent.objects.filter(coupon=coupon, order_id=snapshot.order_id,
                kind='CONSUME').values_list('discount_fen', flat=True).first()
    if discount:
        CouponEvent.objects.create(coupon=coupon, member_id=snapshot.member_id,
            order_id=snapshot.order_id, kind='RESTORE', discount_fen=discount)
    _ledger(account, 'COUPON_RESTORE', 0, f'couponreturn:{snapshot.order_id}', order_id=snapshot.order_id, cause_ref=cause_ref)
    settlement.coupon_restored = True


def reconcile_order_benefits_locked(order, *, fulfilled, active_aftersale, refunds,
                                    shipping_refunded_fen=0, source_ref=None, at=None):
    _atomic()
    at = at or timezone.now()
    snapshot = OrderBenefitSnapshot.objects.filter(order_id=order.id).first()
    if snapshot is None:
        return {'status': 'SKIPPED', 'reason': 'MISSING_HISTORICAL_SNAPSHOT'}
    if snapshot.member_id != order.member_id or order.status != 'PAID':
        raise BenefitError('权益结算订单身份或状态不一致。', 'BENEFIT_ORDER_INVALID', 500)
    member = lock_consumption_member(snapshot.member_id)
    # Coupon precedes account as in reserve/consume/release.
    if snapshot.coupon_id:
        list(MemberCoupon.objects.select_for_update().filter(pk=snapshot.coupon_id))
    account = PointsAccount.objects.select_for_update().get(member=member)
    grants = list(PointsGrant.objects.select_for_update().filter(member=member, available_points__gt=0).order_by('expires_at', 'id'))
    normalize_resolved_lots_locked(account, grants, at)
    settlement, _ = OrderBenefitSettlement.objects.get_or_create(order_id=order.id)
    settlement = OrderBenefitSettlement.objects.select_for_update().get(order_id=order.id)
    cumulative, returned, full = _refund_targets(snapshot, refunds)
    previous = {row['lineId']: row for row in settlement.refunded_lines}
    if any(row['quantity'] < old['quantity'] or row['amountFen'] < old['amountFen']
           for line_id, old in previous.items()
           for row in [cumulative.get(line_id, {'quantity': 0, 'amountFen': 0})]):
        raise BenefitError('累计退款不能倒退。', 'BENEFIT_REFUND_REGRESSED', 500)
    if type(shipping_refunded_fen) is not int or not 0 <= shipping_refunded_fen <= order.payable_fen:
        raise BenefitError('退款运费摘要不正确。', 'BENEFIT_REFUND_INVALID', 500)
    effective = order.payable_fen - sum(row['amountFen'] for row in cumulative.values()) - shipping_refunded_fen
    if effective < 0:
        raise BenefitError('退款总额超过实付。', 'BENEFIT_REFUND_INVALID', 500)
    # An already credited order keeps its prior effective spend during an active case.
    # New confirmed refunds reduce it immediately; incomplete reversal removes it.
    consumption_target = effective if getattr(order,'order_kind','CASH') != 'POINTS' and fulfilled and (not active_aftersale or settlement.credited) else 0
    target = consumption_target // snapshot.earn_unit_fen * snapshot.earn_points
    settlement.revision += 1
    source = source_ref or f'order:{order.id}:{settlement.revision}'
    if getattr(order,'order_kind','CASH') != 'POINTS':
        adjust_effective_spend_locked(member.id, order.id, consumption_target, source,
                                     snapshot.grade_thresholds, policy_revision=snapshot.policy_revision,
                                     activate_rules=fulfilled and not active_aftersale)
    if target > settlement.earned_points:
        grant_ref = f'earn:{order.id}' if settlement.revision == 1 else f'earn:{order.id}:{settlement.revision}'
        _issue_locked(account, target - settlement.earned_points,
                      at + timedelta(days=snapshot.valid_days), grant_ref, order.id, 'EARN', source)
    elif target < settlement.earned_points:
        _clawback(account, snapshot, settlement.earned_points - target, settlement.revision, source)
    settlement.returned_points = _return_deductions(account, snapshot, returned,
                                                  settlement.returned_points, at, source)
    all_cash_refunded = sum(row["amountFen"] for row in cumulative.values()) + shipping_refunded_fen == order.payable_fen
    _restore_coupon(account, snapshot, settlement, full and all_cash_refunded, at, source)
    settlement.credited = fulfilled and (not active_aftersale or settlement.credited)
    settlement.earned_points = target
    settlement.refunded_lines = list(cumulative.values())
    settlement.save()
    return {'status': 'APPLIED', 'effectiveSpendFen': consumption_target,
            'earnedPoints': target, 'returnedPoints': settlement.returned_points,
            'couponRestored': settlement.coupon_restored}


def order_benefit_data(order_id):
    """Order cumulative DTO; caller owns order visibility/authentication."""
    from customers.consumption import order_consumption_fen
    snapshot = OrderBenefitSnapshot.objects.filter(order_id=order_id).first()
    if snapshot is None:
        return {'status': 'SKIPPED', 'reason': 'MISSING_HISTORICAL_SNAPSHOT'}
    settlement = OrderBenefitSettlement.objects.filter(order_id=order_id).first()
    clawed = -(BenefitLedger.objects.filter(order_id=order_id, kind='CLAWBACK')
               .aggregate(value=Sum('amount'))['value'] or 0)
    return {'status': 'SETTLED' if settlement and settlement.credited else 'PENDING',
            'effectiveSpendFen': order_consumption_fen(order_id),
            'earnedPoints': settlement.earned_points if settlement else 0,
            'returnedPoints': settlement.returned_points if settlement else 0,
            'clawedBackPoints': clawed,
            'couponRestored': settlement.coupon_restored if settlement else False,
            'policyRevision': snapshot.policy_revision}


def update_points_policy(*, expected_revision, earn_unit_fen, earn_points,
                         valid_days, refund_valid_days):
    """Trusted admin primitive; caller must authorize and audit configuration."""
    values = [earn_unit_fen, earn_points, valid_days, refund_valid_days]
    if any(type(value) is not int or not 1 <= value <= 1000000 for value in values):
        raise BenefitError('积分配置须为范围内的正整数。')
    with transaction.atomic():
        PointsPolicy.objects.get_or_create(pk=1)
        policy = PointsPolicy.objects.select_for_update().get(pk=1)
        if type(expected_revision) is not int or policy.revision != expected_revision:
            raise BenefitError('积分配置已变化，请刷新。', 'REVISION_CONFLICT', 409)
        policy.earn_unit_fen, policy.earn_points = earn_unit_fen, earn_points
        policy.valid_days, policy.refund_valid_days = valid_days, refund_valid_days
        policy.revision += 1
        policy.save()
        return policy
