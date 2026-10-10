"""Atomic wallet freezes and platform-reviewed offline payment facts."""
import hashlib
import json
import re
import uuid
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from stores.access import StoreError,require_staff
from stores.models import Store,StoreStaff
from payments.models import StoreWallet,StoreWithdrawal,StoreWalletEvent
from customers.models import Member
from wechat_integration.credentials import cipher,CredentialsUnavailable


def withdrawal_data(row):
    return {'id':str(row.id),'amountFen':row.amount_fen,'payeeName':row.payee_name,'bankName':row.bank_name,'bankAccount':'****'+row.bank_account_tail,'status':row.status,'revision':row.revision,'reason':row.reason,'paymentReference':row.payment_reference,'createdAt':row.created_at.isoformat(),'paidAt':row.paid_at.isoformat() if row.paid_at else None}


@sensitive_variables('body')
def validated_request(body):
    if set(body)!={'requestKey','amountFen','payeeName','bankName','bankAccount'} or type(body['amountFen']) is not int or not 1<=body['amountFen']<=9900000000:
        raise StoreError('提现金额或申请参数无效。')
    try:key=uuid.UUID(body['requestKey'])
    except (ValueError,TypeError,AttributeError):raise StoreError('提现请求编号无效。')
    for field,limit in [('payeeName',80),('bankName',120),('bankAccount',34)]:
        if not isinstance(body[field],str) or not body[field].strip() or len(body[field])>limit or any(ord(c)<32 for c in body[field]):raise StoreError('请填写有效的收款信息。')
    if not re.fullmatch(r'[0-9]{8,34}',body['bankAccount']):raise StoreError('银行卡号须为 8 至 34 位数字。')
    return key,hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


@sensitive_variables('body','encrypted')
@transaction.atomic
def request_withdrawal(store,member,body):
    key,digest=validated_request(body)
    Member.objects.select_for_update().get(pk=member.pk)
    Store.objects.select_for_update().get(pk=store.pk)
    list(StoreStaff.objects.select_for_update().filter(store=store,member=member))
    require_staff(member,store.pk,'accounts')
    previous=StoreWithdrawal.objects.filter(store=store,request_key=key).first()
    if previous:
        if previous.member_id!=member.pk or previous.request_digest!=digest:raise StoreError('重复请求内容不一致。','IDEMPOTENCY_CONFLICT',409)
        return previous,False
    wallet,_=StoreWallet.objects.get_or_create(store=store)
    wallet=StoreWallet.objects.select_for_update().get(pk=store.pk)
    if body['amountFen']>wallet.available_fen:raise StoreError('可提现余额不足。','INSUFFICIENT_BALANCE',409)
    try:encrypted=cipher().encrypt(json.dumps({'purpose':'store-withdrawal-bank','storeId':str(store.pk),'requestKey':str(key),'bankAccount':body['bankAccount']}).encode()).decode()
    except CredentialsUnavailable:raise StoreError('收款信息加密服务未配置，暂不能申请提现。','WITHDRAWAL_UNAVAILABLE',503)
    row=StoreWithdrawal.objects.create(store=store,member=member,request_key=key,request_digest=digest,amount_fen=body['amountFen'],payee_name=body['payeeName'].strip(),bank_name=body['bankName'].strip(),encrypted_bank_account=encrypted,bank_account_tail=body['bankAccount'][-4:])
    wallet.available_fen-=row.amount_fen;wallet.frozen_fen+=row.amount_fen;wallet.revision+=1
    wallet.save()
    StoreWalletEvent.objects.create(store=store,withdrawal=row,kind='FREEZE',amount_fen=row.amount_fen,actor_member=member)
    return row,True


@transaction.atomic
def change_withdrawal(row,decision,payment_reference,reason,actor):
    Store.objects.select_for_update().get(pk=row.store_id)
    wallet=StoreWallet.objects.select_for_update().get(pk=row.store_id)
    live=StoreWithdrawal.objects.select_for_update().get(pk=row.pk)
    if live.revision!=row.revision:raise StoreError('提现申请已更新，请刷新。','REVISION_CONFLICT',409)
    if decision not in ['APPROVE','REJECT','PAY']:raise StoreError('提现操作无效。')
    expected='APPROVED_PENDING_PAYMENT' if decision=='PAY' else 'PENDING_REVIEW'
    if live.status!=expected:raise StoreError('提现状态不支持当前操作。','WITHDRAWAL_STATE_CONFLICT',409)
    if not isinstance(reason,str) or len(reason)>300 or (decision=='REJECT' and not reason.strip()):raise StoreError('请填写有效的审核说明。')
    if decision=='PAY' and (not isinstance(payment_reference,str) or not payment_reference.strip() or len(payment_reference)>120):raise StoreError('请填写线下发放流水凭证。')
    if wallet.frozen_fen<live.amount_fen:raise StoreError('冻结资金事实异常，请联系平台。','WALLET_INCONSISTENT',409)
    from payments.models import StoreWithdrawalEvent
    StoreWithdrawalEvent.objects.create(withdrawal=live,action=decision,actor=actor,reason=reason.strip(),payment_reference=payment_reference.strip() if decision=='PAY' else '')
    if decision in ['REJECT','PAY']:
        wallet.frozen_fen-=live.amount_fen
        if decision=='REJECT':wallet.available_fen+=live.amount_fen
        else:wallet.paid_fen+=live.amount_fen
        wallet.revision+=1;wallet.save()
        StoreWalletEvent.objects.create(store_id=live.store_id,withdrawal=live,kind='RELEASE' if decision=='REJECT' else 'OFFLINE_PAY',amount_fen=live.amount_fen,actor_admin=actor)
    live.status={'APPROVE':'APPROVED_PENDING_PAYMENT','REJECT':'REJECTED','PAY':'PAID'}[decision]
    live.reason=reason.strip();live.revision+=1
    if decision=='PAY':live.payment_reference=payment_reference.strip();live.paid_at=timezone.now()
    live.save()
    row.status=live.status;row.revision=live.revision
    return live
