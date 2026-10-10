"""Explicit settlement scanning; read APIs never create monetary facts."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from orders.models import Order
from aftersales.models import AfterSaleCase,OrderAfterSaleSnapshot
from fulfillment.store_service import physical_handoff_fact
from payments.access import applied_receipt
from payments.models import StoreIncome,StoreWallet,StoreWalletEvent,StoreSettlementCursor
from stores.models import Store
from stores.access import StoreError
from .store_finance import order_split


@transaction.atomic
def settle_order(order_id,now=None):
    now=now or timezone.now()
    order=Order.objects.select_for_update().get(pk=order_id)
    if not order.store_id or order.status!=Order.Status.PAID or (order.payable_fen>0 and not applied_receipt(order)):return None
    Store.objects.select_for_update().get(pk=order.store_id)
    income=StoreIncome.objects.select_for_update().filter(order=order).first()
    if income and income.status=='SETTLED':return income
    handoff=physical_handoff_fact(order)
    completed=handoff.confirmed_at if handoff else None
    if not completed:return None
    # Refund cases cannot be treated as an unmodified sale. No invented return cost rule.
    cases=AfterSaleCase.objects.filter(order_line__order=order)
    reason=''
    if cases.filter(status='COMPLETED').exists():reason='订单有退款事实，退款及进货成本分摊等待平台处理。'
    try:
        policy=OrderAfterSaleSnapshot.objects.get(order=order)
        available_at=completed+timedelta(days=policy.received_window_days)
        amounts=order_split(order)
    except (OrderAfterSaleSnapshot.DoesNotExist,StoreError) as exc:
        reason=str(exc) if isinstance(exc,StoreError) else '历史订单缺少售后期快照。'
        available_at=None;amounts={'paid_fen':order.payable_fen,'cost_fen':0,'platform_fen':0,'freight_fen':order.shipping_fee_fen,'store_fen':0}
    if not income:income=StoreIncome(order=order,store_id=order.store_id)
    if reason:
        income.status='HELD';income.reason=reason
        for name,value in amounts.items():setattr(income,name,value)
        income.available_at=available_at;income.save();return income
    for name,value in amounts.items():setattr(income,name,value)
    income.available_at=available_at;income.reason='';income.status='PENDING'
    if available_at>=now or cases.filter(status__in=['PENDING_REVIEW','WAITING_RETURN','WAITING_REFUND']).exists():
        if cases.filter(status__in=['PENDING_REVIEW','WAITING_RETURN','WAITING_REFUND']).exists():income.reason='售后处理中，暂缓结算。'
        income.save();return income
    wallet,_=StoreWallet.objects.get_or_create(store_id=order.store_id)
    wallet=StoreWallet.objects.select_for_update().get(pk=order.store_id)
    wallet.available_fen+=income.store_fen;wallet.revision+=1;wallet.save()
    income.status='SETTLED';income.settled_at=now;income.save()
    StoreWalletEvent.objects.create(store_id=order.store_id,income=income,kind='SETTLE',amount_fen=income.store_fen)
    return income


def scan_settlements(limit=100):
    """Reserve a bounded cursor page, then settle each order independently."""
    from django.db.models import Q
    if type(limit) is not int or not 1<=limit<=10000:raise StoreError('扫描数量无效。')
    with transaction.atomic():
        cursor,_=StoreSettlementCursor.objects.get_or_create(pk=1)
        cursor=StoreSettlementCursor.objects.select_for_update().get(pk=1)
        base=Order.objects.filter(Q(payable_fen=0)|Q(paymentreceipt__applied_at__isnull=False),store__isnull=False,status=Order.Status.PAID).exclude(store_income__status='SETTLED').distinct().order_by('created_at','id')
        rows=base
        if cursor.last_created_at:
            rows=rows.filter(Q(created_at__gt=cursor.last_created_at)|Q(created_at=cursor.last_created_at,id__gt=cursor.last_order_id))
        batch=list(rows[:limit])
        if not batch and cursor.last_created_at:batch=list(base[:limit])
        cursor.last_created_at=batch[-1].created_at if batch else None
        cursor.last_order_id=batch[-1].pk if batch else None
        cursor.save()
    results=[]
    for order in batch:
        row=settle_order(order.pk)
        if row:results.append(row)
    return results
