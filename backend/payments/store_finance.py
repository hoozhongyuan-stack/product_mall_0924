"""Integer-fen calculations and order financial snapshots."""
from stores.access import StoreError
from stores.models import StoreSkuProfitRule
from payments.models import StoreOrderLineFinance


def calculate_split(paid_fen,cost_fen,share_bps,freight_fen):
    if any(type(value) is not int for value in [paid_fen,cost_fen,share_bps,freight_fen]) or min(paid_fen,cost_fen,freight_fen)<0 or not 0<=share_bps<=10000:
        raise StoreError('财务计算参数无效。')
    profit=paid_fen-cost_fen
    if profit<0:raise StoreError('订单利润为负，等待平台处理。','SETTLEMENT_HELD',409)
    # Platform floors to integer fen; store receives the exact remainder.
    platform=profit*share_bps//10000
    store=paid_fen-platform-freight_fen
    if store<0:raise StoreError('前置仓结算金额不足承担运费，等待平台处理。','SETTLEMENT_HELD',409)
    return {'platformFen':platform,'storeFen':store,'costFen':cost_fen,'profitFen':profit,'freightFen':freight_fen}


def snapshot_lines(order,lines):
    if not order.store_id:return
    rules={row.sku_id:row for row in StoreSkuProfitRule.objects.select_for_update().filter(sku_id__in=[line.sku_id for line in lines])}
    rules={line.sku_id:rules[line.sku_id] for line in lines if line.sku_id in rules and rules[line.sku_id].unit_version_id==line.unit_version_id}
    StoreOrderLineFinance.objects.bulk_create([StoreOrderLineFinance(line=line,configured=line.sku_id in rules,purchase_cost_fen=rules[line.sku_id].purchase_cost_fen if line.sku_id in rules else None,platform_share_bps=rules[line.sku_id].platform_share_bps if line.sku_id in rules else None,policy_revision=rules[line.sku_id].revision if line.sku_id in rules else None) for line in lines])


def order_split(order):
    """Allocate gross paid amount including buyer freight across SKU lines."""
    lines=list(order.lines.select_related('store_finance').order_by('sku_id'))
    if not lines:raise StoreError('订单缺少商品财务快照。','SETTLEMENT_HELD',409)
    total=sum(line.payable_fen for line in lines)
    if order.shipping_fee_fen and total==0:raise StoreError('零商品实付订单的运费分配待处理。','SETTLEMENT_HELD',409)
    freight=order.shipping_fee_fen
    allocated={line.id:(freight*line.payable_fen//total if total else 0) for line in lines}
    remainder=freight-sum(allocated.values())
    ranked=sorted(lines,key=lambda line:(-(freight*line.payable_fen%total) if total else 0,str(line.sku_id)))
    for line in ranked[:remainder]:allocated[line.id]+=1
    platform=cost=0
    for line in lines:
        try:snapshot=line.store_finance
        except StoreOrderLineFinance.DoesNotExist:raise StoreError('历史订单缺少财务快照，不自动补算。','SETTLEMENT_HELD',409)
        if not snapshot.configured:raise StoreError('下单时商品规格未配置分润规则。','SETTLEMENT_HELD',409)
        split=calculate_split(line.payable_fen+allocated[line.id],snapshot.purchase_cost_fen*line.quantity,snapshot.platform_share_bps,0)
        platform+=split['platformFen'];cost+=split['costFen']
    store=order.payable_fen-platform-freight
    if store<0:raise StoreError('前置仓结算金额不足承担运费。','SETTLEMENT_HELD',409)
    return {'paid_fen':order.payable_fen,'cost_fen':cost,'platform_fen':platform,'freight_fen':freight,'store_fen':store}
