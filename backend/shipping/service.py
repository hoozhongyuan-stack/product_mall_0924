"""Named pricing entry used by checkout and orders."""

from .models import ShippingPolicy


class ShippingUnavailable(ValueError):
    pass


def current_shipping_policy(*, lock=False):
    queryset = ShippingPolicy.objects
    if lock:
        queryset = queryset.select_for_update()
    policy = queryset.filter(pk=1).first()
    if policy is None:
        raise ShippingUnavailable("配送费配置不可用，请稍后重试。")
    return policy


def delivery_point_eligibility(store, address, policy=None, *, check_time=True):
    """Shared range/time check, used by restaurant assignment and checkout."""
    from .rules import delivery_zone, ShippingRuleError
    from django.utils import timezone
    policy=policy or current_shipping_policy()
    if store.latitude is None or store.longitude is None:
        return '前置仓定位尚未配置。'
    if address.get('latitude') is None or address.get('longitude') is None:
        return '配送到家需要选择已定位的收货地址。'
    zones=[row for row in policy.zones if row['storeId']==str(store.pk)]
    try:
        if not check_time:
            zones=[{**row,'startTime':'00:00','endTime':'23:59'} for row in zones]
        delivery_zone(zones,address,timezone.localtime().strftime('%H:%M'))
    except ShippingRuleError as exc:return str(exc)
    return ''


def shipping_address(member, address_id, dining=None, *, lock=False):
    if dining:
        return {key:dining.get(key) for key in ['latitude','longitude']} | {'province':'','city':'','district':''}
    from customers.addresses import shipping_destination
    return shipping_destination(member,address_id,lock=lock)


def _platform_free(policy, store, mode, amount, now):
    from django.utils.dateparse import parse_datetime
    rule=policy.campaign
    if not rule or not rule.get('enabled') or mode not in rule['deliveryModes'] or amount < rule['minimumFen']:
        return False
    if rule['storeIds'] and (not store or str(store.pk) not in rule['storeIds']):return False
    return parse_datetime(rule['startsAt']) <= now < parse_datetime(rule['endsAt'])


from .courier import region_matches as _region_matches


def quote_shipping(lines, store, mode, address, amount, *, policy=None, now=None):
    """Freeze shipping costs separately from customer fees and sponsorship."""
    import hashlib
    import json
    from django.utils import timezone
    from .rules import delivery_zone, delivery_cost, distance_meters, apply_funding, ShippingRuleError
    policy=policy or current_shipping_policy();now=now or timezone.now()
    base=dict(revision=policy.revision,eligible=True,pending=False,message='',ruleIds=[],costFen=0,customerFeeFen=0,
              warehouseBearsFen=0,platformBearsFen=0,freeSource='NONE',thresholdAmountFen=amount,
              distanceMeters=None,campaignId=None,destinationDigest=hashlib.sha256(json.dumps(address,sort_keys=True).encode()).hexdigest())
    physical=[row for row in lines if row.get('fulfillmentKind')=='SHIP']
    if not physical:return base
    if not store and mode in ('PICKUP','DELIVERY'):return {**base,'eligible':False,'pending':True,'message':'请先选择前置仓后核对配送费用。'}
    if mode=='PICKUP':return base
    actual_mode=mode or 'EXPRESS'
    platform=_platform_free(policy,store,actual_mode,amount,now)
    try:
        if actual_mode=='DELIVERY':
            if not store:raise ShippingRuleError('请选择配送前置仓。')
            if address.get('latitude') is None or address.get('longitude') is None:
                return {**base,'eligible':False,'pending':True,'message':'请选择已定位的收货地址后计算配送费。'}
            if store.latitude is None or store.longitude is None:raise ShippingRuleError('前置仓定位尚未配置。')
            chosen=delivery_zone([row for row in policy.zones if row['storeId']==str(store.pk)],address,timezone.localtime(now).strftime('%H:%M'))
            if amount < chosen['minimumFen']:raise ShippingRuleError('未达到该区域起送门槛，请增加商品或选择其他交付方式。')
            distance=distance_meters(dict(latitude=store.latitude,longitude=store.longitude),address)
            funds=apply_funding(delivery_cost(chosen,distance),amount,chosen['freeThresholdFen'],platform)
            base={**base,**funds,'distanceMeters':int(round(distance)),'ruleIds':[chosen['id']]}
        else:
            if not address.get('province'):
                return {**base,'eligible':False,'pending':True,'message':'请选择收货地址后计算快递运费。'}
            from .courier import quote_courier
            base={**base,**quote_courier(physical,policy,address,amount,platform)}
            base={**base,'platformBearsFen':base['platformBearsFen']+base['warehouseBearsFen'],'warehouseBearsFen':0}
            if base['freeSource']=='WAREHOUSE':base={**base,'freeSource':'PLATFORM'}
        if max(base['costFen'],base['customerFeeFen'])>1_000_000_000:raise ShippingRuleError('配送费用超过上限，请调整选购数量。')
        if base['platformBearsFen']:base['campaignId']='platform-shipping-'+str(policy.revision)
        return base
    except ShippingRuleError as exc:
        return {**base,'eligible':False,'message':str(exc),'customerFeeFen':0,'costFen':0,'warehouseBearsFen':0,'platformBearsFen':0,'freeSource':'NONE'}
