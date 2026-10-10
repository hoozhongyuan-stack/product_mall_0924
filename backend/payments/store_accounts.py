"""Read-only account projections; never lazily settle on GET."""
from django.db.models import Sum
from stores.models import Store
from payments.models import StoreWallet,StoreIncome,StoreWithdrawal
from wechat_integration.credentials import key_available
from .store_withdrawals import withdrawal_data


def income_data(row):
    held=row.status=='HELD'
    return {'id':str(row.pk),'orderId':str(row.order_id),'orderNo':row.order.order_no,'paidFen':row.paid_fen,'costFen':None if held else row.cost_fen,'platformFen':None if held else row.platform_fen,'freightFen':row.freight_fen,'storeFen':None if held else row.store_fen,'status':row.status,'reason':row.reason,'availableAt':row.available_at.isoformat() if row.available_at else None,'settledAt':row.settled_at.isoformat() if row.settled_at else None}


def account_summary(store,wallet,pending,ready):
    return {'storeId':str(store.pk),'storeName':store.name,'settlementReady':True,'withdrawalReady':ready,'reason':'' if ready else '收款信息加密服务尚未配置，暂不能申请提现。','revision':wallet.revision if wallet else 0,'balance':{'pendingFen':pending,'availableFen':wallet.available_fen if wallet else 0,'frozenFen':wallet.frozen_fen if wallet else 0,'paidFen':wallet.paid_fen if wallet else 0},'income':[],'withdrawals':[]}


def account_summaries(stores):
    """Batch list projections with two queries regardless of page size."""
    stores=list(stores)
    if not stores:return []
    ids=[store.pk for store in stores]
    wallets={row.store_id:row for row in StoreWallet.objects.filter(store_id__in=ids)}
    pending={row['store_id']:row['total'] for row in StoreIncome.objects.filter(store_id__in=ids,status='PENDING').values('store_id').annotate(total=Sum('store_fen'))}
    ready=key_available()
    return [account_summary(store,wallets.get(store.pk),pending.get(store.pk,0),ready) for store in stores]


def account_data(store,include_history=True):
    summary=account_summaries([store])[0]
    if not include_history:return summary
    return {**summary,'income':[income_data(row) for row in StoreIncome.objects.filter(store=store).select_related('order').order_by('-order__created_at','id')[:100]],'withdrawals':[withdrawal_data(row) for row in StoreWithdrawal.objects.filter(store=store).order_by('-created_at','id')[:100]]}
