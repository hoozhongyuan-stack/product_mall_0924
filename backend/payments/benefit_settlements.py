"""Explicit authorized zero cash completion; never creates money evidence."""
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from accounts.models import AdminAccount
from accounts.security import permissions, confirm_action, audit
from aftersales.models import BenefitOnlySettlement
from aftersales.returns import _identity_lock
from aftersales.refund_amounts import freeze_refund_amount_locked
from .refunds import _lock_case, _permit, RefundError, _finalize_case
from .models import RefundIntent


def settle_benefits(request,actor,case_id,body,key):
    actor=_permit(actor,"refund.prepare")
    if not isinstance(key,uuid.UUID): raise RefundError("请求标识须为 UUID。")
    if set(body)!={"expectedRevision"} or type(body["expectedRevision"]) is not int:
        raise RefundError("请提交当前售后修订号。")
    with transaction.atomic():
        _identity_lock(actor.id,key,"BENEFIT_SETTLEMENT")
        order,case=_lock_case(case_id)
        live=AdminAccount.objects.select_for_update().get(pk=actor.id)
        if not live.enabled or not {"refund.prepare","aftersale.read"}.issubset(permissions(live)):
            raise RefundError("当前账号没有权益结算权限。","PERMISSION_DENIED",403)
        prior=BenefitOnlySettlement.objects.filter(actor=live,request_key=key).first()
        if prior:
            if prior.case_id != case.id or prior.expected_revision != body["expectedRevision"]:
                raise RefundError("请求标识对应另一项权益结算。","IDEMPOTENCY_CONFLICT",409)
            return case,True,None
        if case.status!="WAITING_REFUND" or case.revision!=body["expectedRevision"] or order.status!="PAID":
            raise RefundError("申请已变化或尚不可结算。","AFTERSALE_CHANGED",409)
        if BenefitOnlySettlement.objects.filter(actor=live,settled_at__gte=timezone.now()-timedelta(minutes=15)).count()>=60:
            raise RefundError("结算操作过多，请稍后重试。","RATE_LIMITED",429)
        snapshot=freeze_refund_amount_locked(order,case)
        if snapshot.total_fen != 0 or RefundIntent.objects.filter(case=case).exists():
            raise RefundError("含现金或运费退款须核实真实退款凭证。","FUNDS_REFUND_REQUIRED",409)
        denied=confirm_action(request,live,"refund.benefits.settle",case.id,case.revision)
        if denied: return None,False,denied
        BenefitOnlySettlement.objects.create(case=case,actor=live,request_key=key,expected_revision=case.revision)
        _finalize_case(case)
        from orders.benefit_lifecycle import reconcile_benefits_locked
        reconcile_benefits_locked(order,source_ref="case:"+str(case.id))
        order.revision+=1;order.save(update_fields=["revision"])
        case.refresh_from_db()
        audit(request,"refund.benefits.settle","aftersale_case",case.id,live,after={"quantity":case.quantity,"amountFen":0})
        return case,False,None
