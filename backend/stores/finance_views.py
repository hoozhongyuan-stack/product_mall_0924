"""Permission checked, password-confirmed offline finance administration."""
import json
import re
from django.views.decorators.debug import sensitive_variables
from cryptography.fernet import InvalidToken
from django.db import transaction
from django.views.decorators.cache import cache_control
from accounts.models import AdminAccount
from accounts.security import require,require_live,confirm_action,audit
from common.http import response,error,method,parse_json_object
from payments.models import StoreWithdrawal
from payments.store_withdrawals import change_withdrawal,withdrawal_data
from stores.models import Store
from stores.access import StoreError
from stores.views import guarded
from wechat_integration.credentials import cipher,CredentialsUnavailable


@sensitive_variables('body','payload')
@cache_control(private=True,no_store=True)
@guarded
def withdrawal_action(request,store_id,withdrawal_id,action):
    bad=method(request,'POST')
    if bad:return bad
    actor,bad=require(request,'stores.accounts.manage')
    if bad:return bad
    body=parse_json_object(request,max_bytes=4096)
    allowed={'review':{'expectedRevision','decision','reason'},'pay':{'expectedRevision','paymentReference','reason'},'payee':{'expectedRevision'}}
    if action not in allowed or set(body)!=allowed[action] or type(body.get('expectedRevision')) is not int or body['expectedRevision']<1:
        raise StoreError('提现操作参数无效。')
    if action=='review' and body['decision'] not in ['APPROVE','REJECT']:raise StoreError('审核操作无效。')
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor,bad=require_live(request,'stores.accounts.manage')
        if bad:return bad
        store=Store.objects.select_for_update().filter(pk=store_id).first()
        if not store:raise StoreError('门店不存在。','STORE_NOT_FOUND',404)
        row=StoreWithdrawal.objects.select_for_update().filter(pk=withdrawal_id,store=store).first()
        if not row:raise StoreError('提现申请不存在。','WITHDRAWAL_NOT_FOUND',404)
        if row.revision!=body['expectedRevision']:return error(request,409,'REVISION_CONFLICT','提现状态已变化，请刷新后重试。')
        bad=confirm_action(request,actor,'stores.withdrawal.'+action,str(row.pk),row.revision)
        if bad:return bad
        before=withdrawal_data(row)
        if action=='payee':
            if row.status not in ['PENDING_REVIEW','APPROVED_PENDING_PAYMENT']:raise StoreError('该申请不能查看收款信息。','WITHDRAWAL_STATE_CONFLICT',409)
            try:
                payload=json.loads(cipher().decrypt(row.encrypted_bank_account.encode()))
                if not isinstance(payload,dict) or payload.get('purpose')!='store-withdrawal-bank' or payload.get('storeId')!=str(store_id) or payload.get('requestKey')!=str(row.request_key) or not isinstance(payload.get('bankAccount'),str) or not re.fullmatch(r'[0-9]{8,34}',payload['bankAccount']):raise ValueError()
            except (CredentialsUnavailable,InvalidToken,ValueError,TypeError,UnicodeError):raise StoreError('收款信息暂不可用，请检查加密服务。','WITHDRAWAL_UNAVAILABLE',503)
            audit(request,'stores.withdrawal.payee','store_withdrawal',row.pk,actor,after={'status':row.status,'revision':row.revision})
            return response(request,{'payeeName':row.payee_name,'bankName':row.bank_name,'bankAccount':payload['bankAccount']})
        row=change_withdrawal(row,body['decision'] if action=='review' else 'PAY',body.get('paymentReference',''),body['reason'],actor)
        after=withdrawal_data(row)
        audit(request,'stores.withdrawal.'+action,'store_withdrawal',row.pk,actor,before=before,after=after)
    return response(request,after)
