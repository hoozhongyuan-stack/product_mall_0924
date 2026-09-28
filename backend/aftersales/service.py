"""Order-first locking; no external money calls or direct stock writes."""
import hashlib
import json
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from accounts.security import permissions
from orders.models import Order, OrderLine
from fulfillment.models import RedeemVoucher, Shipment
from .models import AfterSaleAllocation, AfterSaleCase, AfterSaleEvent, AfterSalePolicy, OrderAfterSaleSnapshot

ACTIVE = ("PENDING_REVIEW", "WAITING_RETURN", "WAITING_REFUND")

class AfterSaleError(ValueError):
    def __init__(self, message, code="VALIDATION_FAILED", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _fail(message, code="AFTERSALE_CONFLICT"):
    raise AfterSaleError(message, code, 409)


def snapshot_order_policy_locked(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Order policy snapshot requires an atomic transaction")
    policy, _ = AfterSalePolicy.objects.get_or_create(pk=1)
    return OrderAfterSaleSnapshot.objects.create(order=order, received_window_days=policy.received_window_days,
                                                policy_revision=policy.revision)


def _lock_line(line_id, member=None):
    identity = OrderLine.objects.filter(pk=line_id).values_list("order_id", flat=True).first()
    order = Order.objects.select_for_update().filter(pk=identity).first()
    if order is None or (member is not None and order.member_id != member.id):
        raise AfterSaleError("订单项不存在。", "ORDER_LINE_NOT_FOUND", 404)
    from customers.consumption import lock_consumption_member
    lock_consumption_member(order.member_id)
    line = OrderLine.objects.get(pk=line_id)
    return order, line


def _allocation(line):
    allocation, _ = AfterSaleAllocation.objects.get_or_create(order_line=line,
        defaults={"purchased_qty": line.quantity, "payable_fen": line.payable_fen})
    return AfterSaleAllocation.objects.select_for_update().get(pk=allocation.pk)


def _eligibility(order, line, kind, scope, quantity, facts=None):
    from payments.access import applied_receipt
    if order.status != Order.Status.PAID or (order.payable_fen > 0 and (facts["receipt"] is None if facts is not None else applied_receipt(order) is None)):
        _fail("订单尚无可信的已收款凭证。", "ORDER_NOT_PAID")
    snapshot = facts["snapshot"] if facts is not None else OrderAfterSaleSnapshot.objects.filter(order=order).first()
    if snapshot is None:
        _fail("订单缺少售后规则快照，需人工处理。", "AFTERSALE_POLICY_MISSING")
    used = unredeemed = 0
    if line.fulfillment_kind == "SHIP":
        if scope != "UNUSED":
            raise AfterSaleError("发货商品不支持核销范围。")
        shipment = facts["shipment"] if facts is not None else Shipment.objects.filter(order=order).first()
        if shipment is not None:
            if kind != "RETURN_REFUND":
                _fail("已发货商品需申请退货退款。", "RETURN_REQUIRED")
            if shipment.confirmed_at and timezone.now() > shipment.confirmed_at + timedelta(days=snapshot.received_window_days):
                _fail("已超过收货后售后时限。", "AFTERSALE_EXPIRED")
        elif kind != "REFUND_ONLY":
            _fail("未发货商品使用仅退款。", "REFUND_ONLY_REQUIRED")
    else:
        if kind != "REFUND_ONLY":
            raise AfterSaleError("核销商品使用仅退款。")
        voucher = facts["voucher"] if facts is not None else RedeemVoucher.objects.select_for_update().filter(order_line=line).first()
        if voucher is None:
            _fail("核销凭证不存在。", "VOUCHER_MISSING")
        completed_used = facts["completed_used"] if facts is not None else sum(AfterSaleCase.objects.filter(order_line=line, status="COMPLETED").values_list("used_quantity", flat=True))
        if scope == "USED":
            if quantity > voucher.redeemed_quantity - completed_used:
                _fail("申请数量超过已核销可售后数量。", "QUANTITY_EXCEEDED")
            used = quantity
        else:
            if voucher.valid_until is None or timezone.localdate() > voucher.valid_until:
                _fail("核销凭证已过期或缺少有效期。", "AFTERSALE_EXPIRED")
            if quantity > line.quantity - voucher.redeemed_quantity - voucher.voided_quantity:
                _fail("申请数量超过未核销数量。", "QUANTITY_EXCEEDED")
            unredeemed = quantity
    return used, unredeemed


def apply_case(member, line_id, kind, quantity, reason, key, redemption_scope="UNUSED"):
    if not member or type(quantity) is not int or quantity <= 0 or kind not in AfterSaleCase.Kind.values:
        raise AfterSaleError("售后类型或数量不正确。")
    if redemption_scope not in ("UNUSED", "USED") or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        raise AfterSaleError("申请原因须为 5 至 500 字，核销范围须有效。")
    try:
        key = uuid.UUID(str(key))
        line_id = uuid.UUID(str(line_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AfterSaleError("请求标识不正确。") from exc
    reason = reason.strip()
    digest = hashlib.sha256(json.dumps([str(line_id), kind, quantity, reason, redemption_scope], ensure_ascii=False).encode()).hexdigest()
    with transaction.atomic():
        order, line = _lock_line(line_id, member)
        # Same member's keys serialize even when retried against a different order.
        from customers.models import Member
        live_member = Member.objects.select_for_update().get(pk=member.id)
        if not live_member.enabled:
            raise AfterSaleError("会员当前不可申请售后。", "MEMBER_DISABLED", 403)
        prior = AfterSaleCase.objects.filter(member=member, request_key=key).first()
        if prior:
            if prior.request_digest != digest:
                _fail("请求标识已用于其他申请。", "IDEMPOTENCY_CONFLICT")
            return prior
        allocation = _allocation(line)
        if AfterSaleCase.objects.filter(order_line=line, status__in=ACTIVE).exists():
            _fail("此订单项已有进行中的售后。", "AFTERSALE_IN_PROGRESS")
        if quantity + allocation.refunded_qty > line.quantity:
            _fail("申请数量超过可售后数量。", "QUANTITY_EXCEEDED")
        amount = (line.payable_fen * (allocation.refunded_qty + quantity) // line.quantity) - allocation.refunded_fen
        used, unredeemed = _eligibility(order, line, kind, redemption_scope, quantity)
        case = AfterSaleCase.objects.create(order_line=line, member=member, request_key=key,
            request_digest=digest, quantity=quantity, amount_fen=amount, used_quantity=used,
            unredeemed_quantity=unredeemed, kind=kind, reason=reason)
        allocation.reserved_qty, allocation.reserved_fen = quantity, amount
        allocation.save(update_fields=["reserved_qty", "reserved_fen"])
        AfterSaleEvent.objects.create(case=case, action="APPLY", status=case.status, reason=reason)
        _bump_order(order)
        _reconcile(order,case)
        return case


def _bump_order(order):
    order.revision += 1
    order.save(update_fields=["revision"])


def _locked_case(case_id):
    try:
        case_id = uuid.UUID(str(case_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AfterSaleError("申请标识不正确。") from exc
    line_id = AfterSaleCase.objects.filter(pk=case_id).values_list("order_line_id", flat=True).first()
    order, line = _lock_line(line_id)
    return order, line, AfterSaleCase.objects.select_for_update().get(pk=case_id)


def _transition(case, target, actor, reason, release=False):
    if release:
        allocation = _allocation(case.order_line)
        allocation.reserved_qty -= case.quantity
        allocation.reserved_fen -= case.amount_fen
        allocation.save(update_fields=["reserved_qty", "reserved_fen"])
    case.status, case.revision = target, case.revision + 1
    case.save(update_fields=["status", "revision", "updated_at"])
    AfterSaleEvent.objects.create(case=case, action=target, status=target, actor=actor, reason=reason)
    return case


def review_case(case_id, actor, approve, reason, expected_revision):
    from accounts.models import AdminAccount
    actor = AdminAccount.objects.filter(pk=getattr(actor, "pk", None), enabled=True).first()
    if actor is None or "aftersale.review" not in permissions(actor):
        raise AfterSaleError("当前账号无售后审核权限。", "PERMISSION_DENIED", 403)
    if type(approve) is not bool or not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
        raise AfterSaleError("审核原因须为 5 至 500 字。")
    with transaction.atomic():
        order, _, case = _locked_case(case_id)
        actor = AdminAccount.objects.select_for_update().get(pk=actor.pk)
        if not actor.enabled or "aftersale.review" not in permissions(actor):
            raise AfterSaleError("当前账号无售后审核权限。", "PERMISSION_DENIED", 403)
        if type(expected_revision) is not int or expected_revision != case.revision or case.status != "PENDING_REVIEW":
            _fail("申请已变化，请刷新。", "AFTERSALE_CHANGED")
        target = ("WAITING_RETURN" if case.kind == "RETURN_REFUND" else "WAITING_REFUND") if approve else "REJECTED"
        result = _transition(case, target, actor, reason.strip(), release=not approve)
        _bump_order(order)
        if approve:
            from .refund_amounts import freeze_refund_amount_locked
            if result.status == "WAITING_REFUND": freeze_refund_amount_locked(order,result)
        _reconcile(order,result)
        return result


def withdraw_case(case_id, member, expected_revision):
    with transaction.atomic():
        order, _, case = _locked_case(case_id)
        if not member or case.member_id != member.id:
            raise AfterSaleError("申请不存在。", "AFTERSALE_NOT_FOUND", 404)
        from customers.models import Member
        live = Member.objects.select_for_update().get(pk=member.pk)
        if not live.enabled:
            raise AfterSaleError("会员当前不可撤销售后。", "MEMBER_DISABLED", 403)
        if type(expected_revision) is not int or expected_revision != case.revision or case.status != "PENDING_REVIEW":
            _fail("只能撤销待审核申请。", "AFTERSALE_CHANGED")
        result = _transition(case, "WITHDRAWN", None, "会员撤销申请", release=True)
        _bump_order(order)
        _reconcile(order,result)
        return result


def finish_refund_locked(case):
    """Caller holds Order/case locks and applies domain side effects in this transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Refund completion requires an atomic transaction")
    case = AfterSaleCase.objects.select_for_update().get(pk=case.pk)
    if case.status == "COMPLETED":
        return case
    if case.status != "WAITING_REFUND":
        _fail("售后尚不允许退款。", "REFUND_NOT_READY")
    allocation = _allocation(case.order_line)
    from .returns import effective_refund
    quantity, amount = effective_refund(case)
    allocation.reserved_qty -= quantity
    allocation.reserved_fen -= amount
    allocation.refunded_qty += quantity
    allocation.refunded_fen += amount
    allocation.save(update_fields=["reserved_qty", "reserved_fen", "refunded_qty", "refunded_fen"])
    result = _transition(case, "COMPLETED", None, "退款或权益结算确认")
    return result


def _reconcile(order,case):
    from orders.benefit_lifecycle import reconcile_benefits_locked
    reconcile_benefits_locked(order,source_ref="case:"+str(case.id))
