from accounts.security import require, parse_json
from accounts.views import method
from aftersales.views import fail, result, key
from .offline_refunds import prepare_reconciliation, confirm_reconciliation
from .refunds import RefundError


def prepare_view(request, case_id):
    bad = method(request, "POST")
    if bad:
        return bad
    a, bad = require(request, "refund.prepare")
    if bad:
        return bad
    _, bad = require(request, "aftersale.read")
    if bad:
        return bad
    try:
        d, replay = prepare_reconciliation(
            request, a, case_id, parse_json(request), key(request)
        )
        return result(request, d, status=200 if replay else 201)
    except ValueError as exc:
        return fail(request, exc)


def confirm_view(request, reconciliation_id):
    bad = method(request, "POST")
    if bad:
        return bad
    a, bad = require(request, "refund.offline.confirm")
    if bad:
        return bad
    _, bad = require(request, "aftersale.read")
    if bad:
        return bad
    try:
        if parse_json(request):
            raise RefundError("请使用已保存的退款核对资料，确认时不重新提交资金字段。")
        d, denied = confirm_reconciliation(request, a, reconciliation_id)
        return denied if denied else result(request, d)
    except ValueError as exc:
        return fail(request, exc)


def settle_benefits_view(request,case_id):
    bad=method(request,"POST")
    if bad: return bad
    actor,bad=require(request,"refund.prepare")
    if bad: return bad
    _,bad=require(request,"aftersale.read")
    if bad: return bad
    try:
        from .benefit_settlements import settle_benefits
        from aftersales.api_read import case_data
        case,replay,denied=settle_benefits(request,actor,case_id,parse_json(request),key(request))
        return denied if denied else result(request,case_data(case,admin=True,actor=actor),status=200 if replay else 201)
    except ValueError as exc:
        return fail(request,exc)
