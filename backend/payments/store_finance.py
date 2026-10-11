"""Integer-fen calculations and order financial snapshots."""
from stores.access import StoreError
from stores.models import StoreSkuProfitRule, StoreProductProfitRule
from stores.profit_access import effective_share
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
    rules = {row.sku_id: row for row in StoreSkuProfitRule.objects.select_for_update().filter(sku_id__in=[line.sku_id for line in lines])}
    product_ids = {line.product_id for line in lines}
    shares = {row.product_id: row for row in StoreProductProfitRule.objects.select_for_update().filter(pk__in=product_ids)}
    all_legacy = list(StoreSkuProfitRule.objects.filter(sku__product_id__in=product_ids).select_related('sku'))
    policies = {product_id: effective_share(product_id, [rule for rule in all_legacy if rule.sku.product_id == product_id], shares.get(product_id)) for product_id in product_ids}
    snapshots = []
    for line in lines:
        rule = rules.get(line.sku_id)
        ratio, share_revision, _ = policies[line.product_id]
        configured = bool(rule and rule.unit_version_id == line.unit_version_id and ratio is not None)
        snapshots.append(StoreOrderLineFinance(line=line, configured=configured,
            purchase_cost_fen=rule.purchase_cost_fen if configured else None,
            platform_share_bps=ratio if configured else None,
            policy_revision=rule.revision if configured else None,
            cost_source='GLOBAL_DEFAULT' if configured else 'UNCONFIGURED'))
    StoreOrderLineFinance.objects.bulk_create(snapshots)


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
    dining=0
    if getattr(order,'store_snapshot',{}).get('dining'):
        from dining_alliance.models import DiningOrderSnapshot
        snapshot=DiningOrderSnapshot.objects.filter(order=order).first()
        if snapshot is None or snapshot.funding_source!='FULFILLMENT_STORE_PROFIT' or not snapshot.configured or snapshot.store_id!=order.store_id:
            raise StoreError('餐饮佣金资金来源或成交快照未确认，双方结算等待人工处理。','SETTLEMENT_HELD',409)
        rows=list(snapshot.lines.select_related('line__store_finance'))
        if len(rows)!=len(lines) or {row.line_id for row in rows}!={line.pk for line in lines}:
            raise StoreError('餐饮佣金商品快照不完整，双方结算等待人工处理。','SETTLEMENT_HELD',409)
        for row in rows:
            source=row.line.store_finance
            if not row.configured or row.purchase_cost_fen!=source.purchase_cost_fen or row.cost_source!=source.cost_source or row.cost_revision!=source.policy_revision or row.quantity!=row.line.quantity or row.paid_fen!=row.line.payable_fen:
                raise StoreError('餐饮和前置仓成本快照不一致，双方结算等待人工处理。','SETTLEMENT_HELD',409)
        dining=snapshot.commission_fen
        if sum(row.commission_fen for row in rows)!=dining or dining>store-cost:
            raise StoreError('餐饮佣金超过前置仓可承担利润，不能扣减进货成本本金，双方结算冻结。','SETTLEMENT_HELD',409)
        store-=dining
    return {'paid_fen':order.payable_fen,'cost_fen':cost,'platform_fen':platform,'freight_fen':freight,'store_fen':store,'dining_commission_fen':dining}
