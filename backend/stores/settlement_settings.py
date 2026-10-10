"""Configure the existing order aftersale window; no invented settlement rules."""
from django.db import transaction
from django.views.decorators.cache import cache_control
from accounts.models import AdminAccount
from accounts.security import audit,confirm_action,require,require_live
from common.http import error,method,parse_json_object,response
from aftersales.policy_settings import current_policy,policy_data,configure_policy_locked
from aftersales.service import AfterSaleError
from .access import StoreError
from .views import guarded


@cache_control(private=True,no_store=True)
@guarded
def settlement_settings(request):
    bad=method(request,'GET','PUT')
    if bad:return bad
    actor,bad=require(request,'stores.manage')
    if bad:return bad
    try:
        if request.method=='GET':return response(request,policy_data())
        body=parse_json_object(request,max_bytes=2048)
        if (set(body)!={'expectedRevision','receivedWindowDays'} or type(body['expectedRevision']) is not int
                or body['expectedRevision']<1 or type(body['receivedWindowDays']) is not int
                or not 1<=body['receivedWindowDays']<=365):
            raise StoreError('售后期须为 1 至 365 天的整数，请检查配置参数。')
        with transaction.atomic():
            AdminAccount.objects.select_for_update().get(pk=actor.pk)
            actor,bad=require_live(request,'stores.manage')
            if bad:return bad
            row=current_policy(lock=True)
            if row.revision!=body['expectedRevision']:
                return error(request,409,'REVISION_CONFLICT','售后期配置已变化，请刷新后重试。')
            bad=confirm_action(request,actor,'stores.settlement.configure','aftersale-policy',row.revision)
            if bad:return bad
            before=policy_data(row)
            result=configure_policy_locked(row,body['receivedWindowDays'])
            audit(request,'stores.settlement.configure','aftersale_policy','aftersale-policy',actor,before=before,after=result)
        return response(request,result)
    except AfterSaleError as exc:
        return error(request,exc.status,exc.code,str(exc))
