"""Order-first return logistics and irrevocable warehouse/refund decisions."""
import hashlib
import json
import uuid
from datetime import timedelta
from django.utils import timezone
from django.db import connection, transaction
from accounts.models import AdminAccount
from accounts.security import permissions
from customers.models import Member
from .models import ReturnShipment, ReturnAcceptance
from .service import AfterSaleError, _locked_case, _allocation, _transition, _bump_order


def effective_refund(case):
    row = ReturnAcceptance.objects.filter(case_id=case.id).first()
    return (row.refund_quantity,row.refund_amount_fen) if row else (case.quantity,case.amount_fen)


def _text(value, limit, minimum=1):
    return isinstance(value,str) and minimum <= len(value.strip()) <= limit and not any(ord(c)<32 for c in value)


def _key(value):
    if not isinstance(value,uuid.UUID):
        raise AfterSaleError("请提供 UUID 防重复标识。")


def _digest(body):
    return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()


def _identity_lock(actor_id,key,domain):
    value=int.from_bytes(hashlib.sha256(f"{domain}:{actor_id}:{key}".encode()).digest()[:8],"big",signed=True)
    with connection.cursor() as cursor: cursor.execute("SELECT pg_advisory_xact_lock(%s)",[value])


def _permit(actor):
    live=AdminAccount.objects.filter(pk=getattr(actor,"pk",None),enabled=True).first()
    if not live or "aftersale.return.accept" not in permissions(live):
        raise AfterSaleError("当前账号无退货验收权限。","PERMISSION_DENIED",403)
    return live


def _ready(case, revision):
    if type(revision) is not int or revision != case.revision or case.status != "WAITING_RETURN" or case.kind != "RETURN_REFUND":
        raise AfterSaleError("申请已变化或尚不可处理退货，请刷新。","AFTERSALE_CHANGED",409)


def submit_return_shipment(case_id,member,body,key):
    _key(key)
    if set(body)!={"expectedRevision","carrierName","trackingNo"} or not _text(body["carrierName"],80) or not _text(body["trackingNo"],80):
        raise AfterSaleError("请填写有效承运商和运单号（各不超过80字）。")
    digest=_digest(body)
    with transaction.atomic():
        _identity_lock(member.id,key,"RETURN_SHIPMENT")
        order,_,case=_locked_case(case_id)
        live=Member.objects.select_for_update().get(pk=member.pk)
        if case.member_id!=live.id:
            raise AfterSaleError("申请不存在。","AFTERSALE_NOT_FOUND",404)
        if not live.enabled: raise AfterSaleError("会员当前不可提交退货物流。","MEMBER_DISABLED",403)
        prior=ReturnShipment.objects.filter(member=live,request_key=key).first()
        if prior:
            if prior.case_id!=case.id or prior.request_digest!=digest:
                raise AfterSaleError("请求标识已用于其他物流资料。","IDEMPOTENCY_CONFLICT",409)
            return prior,True
        _ready(case,body["expectedRevision"])
        row=ReturnShipment.objects.create(case=case,member=live,request_key=key,request_digest=digest,
            expected_revision=case.revision,revision=case.revision+1,carrier_name=body["carrierName"].strip(),tracking_no=body["trackingNo"].strip())
        _transition(case,"WAITING_RETURN",None,"会员提交退货物流")
        _bump_order(order)
        from .service import _reconcile
        if case.status == "WAITING_REFUND":
            from .refund_amounts import freeze_refund_amount_locked
            freeze_refund_amount_locked(order,case)
        _reconcile(order,case)
        return row,False


def _decision(case,body):
    fields={"expectedRevision","mode","receivedQuantity","salableQuantity","refundQuantity","refundAmountFen","reason"}
    if set(body)!=fields: raise AfterSaleError("验收字段不正确。")
    _ready(case,body["expectedRevision"])
    mode=body["mode"]
    received,salable,qty=(body[n] for n in ("receivedQuantity","salableQuantity","refundQuantity"))
    if mode not in ("RECEIVED","WAIVED_RETURN") or any(type(v) is not int or not 0<=v<=case.quantity for v in (received,salable,qty)):
        raise AfterSaleError("验收类型或数量不正确。")
    if salable>received or (mode=="RECEIVED" and qty>received) or (mode=="WAIVED_RETURN" and (received or salable)):
        raise AfterSaleError("可售和退款数量须符合收货事实；免寄回不登记收货或回库。")
    if not _text(body["reason"],500,5): raise AfterSaleError("验收、差异及免寄回原因须为5至500字。")
    allocation=_allocation(case.order_line)
    maximum=min(case.amount_fen, max(0,case.order_line.payable_fen*(allocation.refunded_qty+qty)//case.order_line.quantity-allocation.refunded_fen)) if qty else 0
    amount=body["refundAmountFen"] if body["refundAmountFen"] is not None else maximum
    if type(amount) is not int or (qty and ((maximum > 0 and not 0 < amount <= maximum) or (maximum == 0 and amount != 0))) or (not qty and amount!=0):
        raise AfterSaleError("退款金额须大于零且不超过服务端成交快照计算范围；零批准数量的金额必须为零。")
    return {"mode":mode,"received_quantity":received,"salable_quantity":salable,"refund_quantity":qty,
            "refund_amount_fen":amount,"max_refund_amount_fen":maximum,"reason":body["reason"].strip()}


def acceptance_data(row,admin=False):
    result={"acceptanceId":str(row.id),"mode":row.mode,"receivedQuantity":row.received_quantity,
        "salableQuantity":row.salable_quantity,"damagedQuantity":row.received_quantity-row.salable_quantity,
        "refundQuantity":row.refund_quantity,"refundAmountFen":row.refund_amount_fen,
        "refundPoints":row.refund_quantity*row.case.order_line.points_unit_price,
        "maxRefundAmountFen":row.max_refund_amount_fen,"reason":row.reason,"acceptedAt":row.accepted_at.isoformat()}
    if admin: result["actorName"]=row.actor.display_name
    return result


def shipment_data(row):
    return {"shipmentId":str(row.id),"carrierName":row.carrier_name,"trackingNo":row.tracking_no,
            "revision":row.revision,"submittedAt":row.submitted_at.isoformat()}


def preview_return_acceptance(case_id,actor,body):
    _permit(actor)
    with transaction.atomic():
        _,line,case=_locked_case(case_id)
        v=_decision(case,body)
        return {"caseId":str(case.id),"revision":case.revision,"refundQuantity":v["refund_quantity"],
                "refundAmountFen":v["refund_amount_fen"],"maxRefundAmountFen":v["max_refund_amount_fen"],
                "refundPoints":v["refund_quantity"]*line.points_unit_price}


def accept_return(case_id,actor,body,key):
    _key(key); actor=_permit(actor); digest=_digest(body)
    with transaction.atomic():
        _identity_lock(actor.id,key,"RETURN_ACCEPTANCE")
        order,line,case=_locked_case(case_id)
        live=AdminAccount.objects.select_for_update().get(pk=actor.pk)
        _permit(live)
        prior=ReturnAcceptance.objects.filter(actor=live,request_key=key).first()
        if prior:
            if prior.case_id!=case.id or prior.request_digest!=digest:
                raise AfterSaleError("请求标识对应其他验收结论。","IDEMPOTENCY_CONFLICT",409)
            return prior,True
        if ReturnAcceptance.objects.filter(actor=live,accepted_at__gte=timezone.now()-timedelta(minutes=15)).count()>=60:
            raise AfterSaleError("验收操作过多，请稍后重试。","RATE_LIMITED",429)
        values=_decision(case,body)
        row=ReturnAcceptance.objects.create(case=case,actor=live,request_key=key,request_digest=digest,expected_revision=case.revision,**values)
        from inventory.refunds import record_return_disposition_locked
        record_return_disposition_locked(line,row,live)
        allocation=_allocation(line)
        allocation.reserved_qty-=case.quantity-row.refund_quantity
        allocation.reserved_fen-=case.amount_fen-row.refund_amount_fen
        allocation.save(update_fields=["reserved_qty","reserved_fen"])
        _transition(case,"WAITING_REFUND" if row.refund_quantity else "REJECTED",live,row.reason)
        _bump_order(order)
        from .service import _reconcile
        if case.status == "WAITING_REFUND":
            from .refund_amounts import freeze_refund_amount_locked
            freeze_refund_amount_locked(order,case)
        _reconcile(order,case)
        return row,False
