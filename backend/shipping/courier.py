"""Group a basket by its product templates and sum each group's shipping cost."""
from .rules import courier_cost,apply_funding,ShippingRuleError


def region_matches(region,address):
    return all(address.get(row['level'])==row['name'] for row in [*region.get('ancestors',[]),region])


def group_lines(lines,templates,address):
    active=[row for row in templates if row['enabled']]
    defaults=[row for row in active if not row['productIds']]
    groups={}
    for line in lines:
        matches=[row for row in active if line['productId'] in row['productIds']]
        if len(matches)>1 or (not matches and len(defaults)!=1):
            raise ShippingRuleError('商品快递模板尚未正确配置。')
        chosen=(matches or defaults)[0]
        blocked=any(region_matches(row,address) for row in chosen['blockedRegions'])
        allowed=not chosen['regions'] or any(region_matches(row,address) for row in chosen['regions'])
        if blocked or not allowed:
            raise ShippingRuleError('收货地区不支持快递发货，请更换地址或交付方式。')
        prior=groups.get(chosen['id'],(chosen,[]))
        groups={**groups,chosen['id']:(chosen,[*prior[1],line])}
    return groups


def weight_for_group(items,weights,versions):
    if any(row['skuId'] not in weights or weights[row['skuId']]['unitVersionId']!=versions.get(row['skuId']) for row in items):
        raise ShippingRuleError('商品销售单位重量尚未配置或已变化，请联系平台。')
    return sum(weights[row['skuId']]['weightGrams']*row['quantity'] for row in items)


def quote_courier(lines,policy,address,amount,platform):
    from catalog.shipping_access import shipping_unit_versions
    groups=group_lines(lines,policy.templates,address)
    weights={row['skuId']:row for row in policy.sku_weights}
    versions=shipping_unit_versions([row['skuId'] for row in lines])
    result=dict(costFen=0,customerFeeFen=0,warehouseBearsFen=0,platformBearsFen=0,ruleIds=[])
    sources=set()
    for identifier,(chosen,items) in sorted(groups.items()):
        grams=weight_for_group(items,weights,versions) if chosen['mode']=='WEIGHT' else None
        cost=courier_cost(chosen,sum(row['quantity'] for row in items),grams)
        funds=apply_funding(cost,amount,chosen['freeThresholdFen'],platform)
        result={**{key:result[key]+funds[key] for key in ['costFen','customerFeeFen','warehouseBearsFen','platformBearsFen']},'ruleIds':[*result['ruleIds'],identifier]}
        sources={*sources,funds['freeSource']}
    return {**result,'freeSource':next(iter(sources)) if len(sources)==1 else 'MIXED'}
