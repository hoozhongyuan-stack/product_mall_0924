"""Named member consumption boundary. Caller locks order, then member."""
from django.db import connection
from django.utils import timezone

from catalog.models import MemberGrade
from .models import (Member, GradeThreshold, MemberConsumption, ConsumptionOrder,
                     ConsumptionEvent)

DEFAULT_THRESHOLDS = {'normal': 0, 'silver': 50000, 'gold': 200000}


def snapshot_grade_thresholds():
    configured = dict(GradeThreshold.objects.values_list('grade_id', 'minimum_spend_fen'))
    return [{'gradeId': str(grade.id), 'rank': grade.rank,
             'minimumSpendFen': configured.get(grade.id, DEFAULT_THRESHOLDS.get(grade.code, 0))}
            for grade in MemberGrade.objects.filter(enabled=True).order_by('rank')
            if grade.id in configured or grade.code in DEFAULT_THRESHOLDS]


def lock_consumption_member(member_id):
    if not connection.in_atomic_block:
        raise ValueError('Consumption requires the order transaction.')
    return Member.objects.select_for_update().get(pk=member_id)


def adjust_effective_spend_locked(member_id, order_id, target_fen, source_ref, thresholds, *, policy_revision=1,
                                  activate_rules=None):
    if type(target_fen) is not int or target_fen < 0:
        raise ValueError('Effective consumption must be a nonnegative integer.')
    member = lock_consumption_member(member_id)
    account, _ = MemberConsumption.objects.get_or_create(member=member)
    row, _ = ConsumptionOrder.objects.get_or_create(order_id=order_id, defaults={'member': member})
    if row.member_id != member.id:
        raise ValueError('Order consumption belongs to another member.')
    delta = target_fen - row.effective_spend_fen
    activate = (target_fen > 0 if activate_rules is None else activate_rules) and policy_revision > account.grade_policy_revision
    if not delta and not activate:
        return account
    balance = account.effective_spend_fen + delta
    if balance < 0:
        raise ValueError('Consumption balance inconsistent.')
    if activate:
        account.grade_policy_revision = policy_revision
        account.grade_thresholds = thresholds
    active_thresholds = account.grade_thresholds or thresholds
    eligible = [rule for rule in active_thresholds if rule['minimumSpendFen'] <= balance]
    before = member.grade_id
    after = max(eligible, key=lambda rule: rule['rank'])['gradeId'] if eligible else str(before)
    member.grade_id = after
    member.save(update_fields=['grade', 'updated_at'])
    account.effective_spend_fen = balance
    if str(before) != str(after):
        account.grade_effective_at = timezone.now()
    account.save()
    row.effective_spend_fen = target_fen
    row.save(update_fields=['effective_spend_fen'])
    ConsumptionEvent.objects.create(member=member, order_id=order_id, amount_fen=delta,
        balance_fen=balance, grade_id_before=before, grade_id_after=after, source_ref=source_ref)
    return account


def order_consumption_fen(order_id):
    return (ConsumptionOrder.objects.filter(order_id=order_id)
            .values_list('effective_spend_fen', flat=True).first() or 0)
