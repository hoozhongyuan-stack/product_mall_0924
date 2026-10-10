"""Persistent scoped store observations, deliberately separate from platform refund approvals."""
import uuid
from django.db import transaction
from .models import StoreAfterSaleNote
from .service import AfterSaleError, _locked_case
from .returns import _identity_lock, _digest


def notes_data(case):
    return [{'id':str(row.id),'kind':row.kind,'note':row.note,'receivedQuantity':row.received_quantity,
        'salableQuantity':row.salable_quantity,'occurredAt':row.occurred_at.isoformat(),
        'platformReviewRequired':row.kind=='RECEIPT'} for row in case.store_notes.order_by('-occurred_at','-id')[:30]]


def record_note(member,store_id,case_id,body,key):
    from stores.access import require_staff
    from stores.models import StoreStaff
    fields={'kind','note','expectedRevision','receivedQuantity','salableQuantity'}
    if not isinstance(key,uuid.UUID) or not isinstance(body,dict) or set(body)-fields or not {'kind','note','expectedRevision'} <= set(body):
        raise AfterSaleError('售后记录字段不正确。')
    kind,note=body['kind'],body['note']
    received,salable=body.get('receivedQuantity',0),body.get('salableQuantity',0)
    if kind not in ('ADVICE','RECEIPT') or not isinstance(note,str) or not 5 <= len(note.strip()) <= 500 or type(received) is not int or type(salable) is not int or not 0 <= salable <= received <= 9999:
        raise AfterSaleError('请填写有效售后建议或收货记录。')
    if kind=='ADVICE' and (received or salable):
        raise AfterSaleError('售后建议不能登记收货数量。')
    digest=_digest({'storeId':str(store_id),'caseId':str(case_id),**body})
    with transaction.atomic():
        _identity_lock('store-aftersale',key,'STORE_AFTERSALE')
        require_staff(member,store_id,'orders')
        order,line,case=_locked_case(case_id)
        list(StoreStaff.objects.select_for_update().filter(store_id=store_id,member=member))
        require_staff(member,store_id,'orders')
        if str(order.store_id)!=str(store_id):
            raise AfterSaleError('售后单不存在。','AFTERSALE_NOT_FOUND',404)
        prior=StoreAfterSaleNote.objects.filter(request_key=key).first()
        if prior:
            if prior.actor_id!=member.id or prior.request_digest!=digest:
                raise AfterSaleError('请求标识对应其他记录。','IDEMPOTENCY_CONFLICT',409)
            return prior
        if type(body['expectedRevision']) is not int or body['expectedRevision']!=case.revision:
            raise AfterSaleError('售后单已变化，请刷新后重试。','AFTERSALE_CHANGED',409)
        if case.status not in ('PENDING_REVIEW','WAITING_RETURN','WAITING_REFUND'):
            raise AfterSaleError('该售后单已结束。','AFTERSALE_CLOSED',409)
        if kind=='RECEIPT' and (case.kind!='RETURN_REFUND' or case.status!='WAITING_RETURN' or not 1 <= received <= case.quantity):
            raise AfterSaleError('只有待退货验收的实物售后单可登记实际收货。')
        return StoreAfterSaleNote.objects.create(case=case,actor=member,kind=kind,note=note.strip(),received_quantity=received,salable_quantity=salable,request_key=key,request_digest=digest)
