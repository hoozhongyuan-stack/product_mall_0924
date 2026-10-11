"""Warehouse profit funded commission snapshots and pure-read maturity."""
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .access import DiningError, require_staff, checkout_context
from .models import DiningOrderSnapshot, DiningOrderLineSnapshot, ProductCommissionRule, Restaurant, Withdrawal


FUNDING_SOURCE = 'FULFILLMENT_STORE_PROFIT'

def commission_fen(paid_fen, unit_cost_fen, quantity, ratio_bps):
    if any(type(v) is not int for v in (paid_fen, unit_cost_fen, quantity, ratio_bps)) or min(paid_fen, unit_cost_fen)<0 or quantity<1 or not 0<=ratio_bps<=10000:
        raise DiningError('佣金计算参数无效。')
    profit = paid_fen-unit_cost_fen*quantity
    if profit<0:
        raise DiningError('订单利润为负，佣金等待人工确认。', 'DINING_COMMISSION_HELD', 409)
    return profit*ratio_bps//10000


@transaction.atomic
def snapshot_order(order, session_id, member, lines):
    from orders.models import Order
    from payments.models import StoreOrderLineFinance
    Order.objects.select_for_update().get(pk=order.pk)
    store, context = checkout_context(session_id, member, lock=True)
    if store.pk != order.store_id:
        raise DiningError('订单履约前置仓与扫码会话不一致。', 'DINING_STORE_MISMATCH', 409)
    if DiningOrderSnapshot.objects.filter(order=order).exists():
        raise DiningError('订单餐饮快照已存在。', 'DINING_SNAPSHOT_EXISTS', 409)
    snap = DiningOrderSnapshot(order=order, session_id=session_id, restaurant_id=context['restaurantId'], table_id=context['tableId'], store=store, restaurant_name=context['name'], address=context['address'], table_number=context['tableNumber'], funding_source=FUNDING_SOURCE)
    line_snapshots = []
    ratios = {r.product_id:r for r in ProductCommissionRule.objects.select_for_update().filter(product_id__in=[line.product_id for line in lines])}
    costs = {row.line_id:row for row in StoreOrderLineFinance.objects.select_related('line').filter(line__in=lines)}
    total, configured = 0, True
    for line in lines:
        cost, ratio = costs.get(line.id), ratios.get(line.product_id)
        valid = bool(cost and cost.configured and ratio and line.warehouse_id == store.warehouse_id)
        amount = 0
        if valid:
            try:
                amount = commission_fen(line.payable_fen, cost.purchase_cost_fen, line.quantity, ratio.commission_bps)
            except DiningError:
                valid = False
        configured &= valid
        total += amount
        line_snapshots.append(DiningOrderLineSnapshot(line=line, snapshot=snap, quantity=line.quantity, paid_fen=line.payable_fen, purchase_cost_fen=cost.purchase_cost_fen if cost else None, cost_source=cost.cost_source if cost else 'UNCONFIGURED', cost_revision=cost.policy_revision if cost else None, commission_bps=ratio.commission_bps if ratio else None, commission_revision=ratio.revision if ratio else None, commission_fen=amount, configured=valid))
    snap.commission_fen, snap.configured = total, configured
    snap.save(force_insert=True)
    DiningOrderLineSnapshot.objects.bulk_create(line_snapshots)
    return snap


def income_data(snap, now=None):
    from aftersales.models import AfterSaleCase, OrderAfterSaleSnapshot
    from fulfillment.store_service import physical_handoff_fact
    from payments.access import applied_receipt
    now = now or timezone.now()
    order = snap.order
    status, reason, available_at, completed = 'ESTIMATED', '', None, None
    if snap.funding_source!='FULFILLMENT_STORE_PROFIT':
        status, reason = 'HELD', '历史订单未确认餐饮佣金资金来源，等待人工处理。'
    elif not snap.configured:
        status, reason = 'HELD', '成本、佣金规则缺失或利润为负，等待人工确认。'
    elif AfterSaleCase.objects.filter(order_line__order=order).exclude(status__in=['REJECTED', 'WITHDRAWN']).exists():
        status, reason = 'HELD', '订单有售后或退款，佣金等待人工确认。'
    elif order.status == 'CLOSED':
        status, reason = 'HELD', '订单已关闭，不可提现。'
    elif order.status == 'PAID' and (order.payable_fen == 0 or applied_receipt(order)):
        fact = physical_handoff_fact(order)
        completed = fact.confirmed_at if fact else None
        if completed:
            status = 'PENDING'
            policy = OrderAfterSaleSnapshot.objects.filter(order=order).first()
            if policy is None:
                status, reason = 'HELD', '订单缺少售后期快照。'
            else:
                available_at = completed + timedelta(days=policy.received_window_days)
                from payments.store_finance import order_split
                from stores.access import StoreError
                from payments.models import StoreIncome
                try:
                    split=order_split(order)
                except StoreError as exc:
                    status,reason='HELD',str(exc)
                else:
                    funded=StoreIncome.objects.filter(order=order).first()
                    if funded and funded.status=='HELD':
                        status,reason='HELD',funded.reason
                    elif available_at < now:
                        if funded and funded.status=='SETTLED' and funded.dining_commission_fen==snap.commission_fen and funded.store_fen==split['store_fen']:
                            status='AVAILABLE'
                        else:
                            reason='已过售后期，等待前置仓净结算和佣金扣除确认。'
    return {'orderId':str(order.id), 'orderNo':order.order_no, 'commissionFen':snap.commission_fen, 'status':status, 'reason':reason, 'availableAt':available_at.isoformat() if available_at else None, 'tableNumber':snap.table_number, 'bookedAt':completed.isoformat() if completed else None, 'createdAt':snap.created_at.isoformat()}


def withdrawal_data(row):
    return {'id':str(row.id), 'amountFen':row.amount_fen, 'status':row.status, 'reason':row.reason, 'revision':row.revision, 'createdAt':row.created_at.isoformat()}


def account_data(restaurant, history_params=None):
    incomes = [income_data(row) for row in DiningOrderSnapshot.objects.filter(restaurant=restaurant).select_related('order').order_by('-created_at','order_id')]
    withdrawals = list(Withdrawal.objects.filter(restaurant=restaurant).order_by('-created_at','id'))
    frozen = sum(row.amount_fen for row in withdrawals if row.status in ('PENDING','APPROVED'))
    mature = sum(row['commissionFen'] for row in incomes if row['status']=='AVAILABLE')
    result = {'matureFen':mature, 'availableFen':max(0,mature-frozen), 'pendingFen':sum(row['commissionFen'] for row in incomes if row['status'] in ('ESTIMATED','PENDING')), 'heldFen':sum(row['commissionFen'] for row in incomes if row['status']=='HELD'), 'totalCommissionFen':sum(row['commissionFen'] for row in incomes), 'frozenFen':frozen, 'incomes':incomes[:100], 'withdrawals':[withdrawal_data(row) for row in withdrawals[:100]], 'payoutEnabled':False, 'fundingSource':'FULFILLMENT_STORE_PROFIT','fundingLabel':'佣金由履约前置仓利润承担'}
    if history_params is not None:
        from .read_history import history_page
        income_page=history_page(incomes,history_params,'income',{'ESTIMATED','PENDING','HELD','AVAILABLE'})
        withdrawal_page=history_page([withdrawal_data(row) for row in withdrawals],history_params,'withdrawal',{'PENDING','APPROVED','REJECTED'})
        result={**result,'incomes':income_page['items'],'withdrawals':withdrawal_page['items'],'incomePagination':{k:v for k,v in income_page.items() if k!='items'},'withdrawalPagination':{k:v for k,v in withdrawal_page.items() if k!='items'}}
    return result


@transaction.atomic
def request_withdrawal(restaurant_id, member, body):
    if set(body) != {'amountFen','requestKey'} or type(body.get('amountFen')) is not int or not 0<body['amountFen']<=10**12:
        raise DiningError('提现金额和请求编号无效。')
    key = uuid.UUID(body['requestKey'])
    lock_restaurant_orders(restaurant_id)
    restaurant = require_staff(member, restaurant_id, 'finance', lock=True)
    previous = Withdrawal.objects.filter(restaurant=restaurant, request_key=key).first()
    if previous:
        if previous.member_id != member.id or previous.amount_fen != body['amountFen']:
            raise DiningError('请求编号已用于其他提现。', 'IDEMPOTENCY_CONFLICT', 409)
        return previous, False
    if body['amountFen'] > account_data(restaurant)['availableFen']:
        raise DiningError('可提现佣金不足。', 'DINING_INSUFFICIENT_BALANCE', 409)
    return Withdrawal.objects.create(restaurant=restaurant, member=member, request_key=key, amount_fen=body['amountFen']), True


@transaction.atomic
def change_withdrawal(restaurant_id, withdrawal_id, action, body):
    lock_restaurant_orders(restaurant_id)
    restaurant = Restaurant.objects.select_for_update().get(pk=restaurant_id)
    row = Withdrawal.objects.select_for_update().filter(pk=withdrawal_id, restaurant=restaurant).first()
    if row is None:
        raise DiningError('提现申请不存在。', 'DINING_NOT_FOUND', 404)
    if set(body)-{'revision','reason'} or type(body.get('revision')) is not int or row.revision!=body['revision']:
        raise DiningError('提现申请已变化，请刷新。', 'REVISION_CONFLICT', 409)
    reason = body.get('reason','')
    if not isinstance(reason,str) or len(reason)>500 or action not in ('approve','reject') or row.status!='PENDING':
        raise DiningError('提现审核状态或说明无效。', 'DINING_INVALID_TRANSITION', 409)
    if action=='reject' and not reason.strip():
        raise DiningError('请填写拒绝原因。')
    if action=='approve':
        balance=account_data(restaurant)
        mature=balance['matureFen']
        if mature < balance['frozenFen']:
            raise DiningError('佣金出现售后或退款，暂不可审核通过。','DINING_COMMISSION_HELD',409)
    row.status = 'APPROVED' if action=='approve' else 'REJECTED'
    row.reason, row.revision = reason.strip(), row.revision+1
    row.save(update_fields=['status','reason','revision'])
    return row


def lock_restaurant_orders(restaurant_id):
    from orders.models import Order
    ids=DiningOrderSnapshot.objects.filter(restaurant_id=restaurant_id).values_list('order_id',flat=True)
    list(Order.objects.select_for_update().filter(pk__in=ids).order_by('id').values_list('pk',flat=True))
