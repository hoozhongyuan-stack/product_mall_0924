"""D2 member logistics and CSRF/session protected warehouse confirmation."""
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from accounts.security import require, confirm_action, audit
from accounts.views import method
from .views import member, key, result, fail, limited
from .models import AfterSaleCase, ReturnAcceptance
from .service import _locked_case, AfterSaleError
from .api_read import case_data
from .returns import submit_return_shipment, preview_return_acceptance, accept_return, _identity_lock, _digest


@csrf_exempt
def return_shipment_view(request,case_id):
    bad=method(request,"POST")
    if bad: return bad
    m,bad=member(request)
    if bad: return bad
    try:
        from accounts.security import parse_json
        body=parse_json(request); request_key=key(request)
        from .models import ReturnShipment
        if not ReturnShipment.objects.filter(member=m,request_key=request_key).exists(): limited(m)
        submit_return_shipment(case_id,m,body,request_key)
        c=AfterSaleCase.objects.select_related("order_line__order","return_acceptance").get(pk=case_id)
        return result(request,case_data(c))
    except ValueError as exc: return fail(request,exc)


def _admin(request):
    actor,bad=require(request,"aftersale.return.accept")
    if bad: return actor,bad
    _,bad=require(request,"aftersale.read")
    return actor,bad


def return_acceptance_preview_view(request,case_id):
    bad=method(request,"POST")
    if bad: return bad
    actor,bad=_admin(request)
    if bad: return bad
    try:
        from accounts.security import parse_json
        return result(request,preview_return_acceptance(case_id,actor,parse_json(request)))
    except ValueError as exc: return fail(request,exc)


def return_acceptance_view(request,case_id):
    bad=method(request,"POST")
    if bad: return bad
    actor,bad=_admin(request)
    if bad: return bad
    try:
        from accounts.security import parse_json
        body=parse_json(request); request_key=key(request)
        with transaction.atomic():
            _identity_lock(actor.id,request_key,"RETURN_ACCEPTANCE")
            _,_,case=_locked_case(case_id)
            prior=ReturnAcceptance.objects.filter(actor=actor,request_key=request_key).first()
            if prior:
                if prior.case_id!=case.id or prior.request_digest!=_digest(body):
                    raise AfterSaleError("请求标识对应其他验收结论。","IDEMPOTENCY_CONFLICT",409)
            else:
                from .returns import _decision
                _decision(case,body)
                denied=confirm_action(request,actor,"aftersale.return.accept",case.id,case.revision)
                if denied: return denied
            row,replay=accept_return(case.id,actor,body,request_key)
            if not replay:
                audit(request,"aftersale.return.accept","aftersale_case",case.id,actor,
                    after={"receivedQuantity":row.received_quantity,"salableQuantity":row.salable_quantity,
                           "refundQuantity":row.refund_quantity,"refundAmountFen":row.refund_amount_fen,"mode":row.mode})
        case=AfterSaleCase.objects.select_related("order_line__order","return_acceptance__actor").get(pk=case_id)
        return result(request,case_data(case,admin=True,actor=actor))
    except ValueError as exc: return fail(request,exc)
