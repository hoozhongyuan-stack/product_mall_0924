"""Bounded configuration validation; all writes coordinated by singleton revision."""
import math
import re
from uuid import UUID
from django.utils.dateparse import parse_datetime
from catalog.models import Product, Sku
from stores.models import Store
from .rules import ShippingRuleError, integer


def identifier(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ShippingRuleError('资源编号格式不正确。') from exc


def name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120:
        raise ShippingRuleError('请填写 1 至 120 字的名称。')
    return value.strip()


def enabled(value):
    if type(value) is not bool:
        raise ShippingRuleError('启停状态须为布尔值。')
    return value


def ids(value, model):
    if not isinstance(value, list) or len(value) > 200:
        raise ShippingRuleError('关联对象不可超过 200 个。')
    normalized = sorted({identifier(item) for item in value})
    if model.objects.filter(pk__in=normalized).count() != len(normalized):
        raise ShippingRuleError('关联对象不存在，请刷新列表。')
    return normalized


def regions(value):
    if not isinstance(value, list) or len(value) > 200:
        raise ShippingRuleError('地区数量不可超过 200 个。')
    result = []
    for item in value:
        if not isinstance(item, dict) or not {'code','name','level'}.issubset(item) or set(item)-{'code','name','level','ancestors'}:
            raise ShippingRuleError('地区须通过省市区选择器选择。')
        if not isinstance(item['code'], str) or not re.fullmatch(r'\d{6}', item['code']) or item['level'] not in ['province','city','district']:
            raise ShippingRuleError('行政区代码或层级无效。')
        from django.core.cache import cache
        verified=cache.get('shipping-verified-region:'+item['code'])
        if not verified:
            from .models import ShippingPolicy
            policy=ShippingPolicy.objects.get(pk=1)
            saved=[region for row in policy.templates for region in row['regions']+row['blockedRegions']]
            verified=next((row for row in saved if row['code']==item['code']),None)
        if not verified or verified['name']!=item['name'] or verified['level']!=item['level']:
            raise ShippingRuleError('地区选择已过期或代码不匹配，请通过地区选择器重新选择。')
        result.append(dict(code=item['code'],name=name(item['name']),level=item['level'],ancestors=verified.get('ancestors',[])))
    if len({row['code'] for row in result}) != len(result):
        raise ShippingRuleError('地区不可重复。')
    return sorted(result, key=lambda row: row['code'])


def point(value):
    if not isinstance(value, dict) or set(value) != {'latitude','longitude'}:
        raise ShippingRuleError('地图坐标不完整。')
    result = {}
    for key, limit in [('latitude',90),('longitude',180)]:
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number) or abs(number) > limit:
            raise ShippingRuleError('地图坐标无效。')
        result[key] = round(number, 6)
    return result


def threshold(value):
    return None if value is None else integer(value, maximum=1_000_000_000)


def template(body):
    fields = {'name','mode','firstFeeFen','firstUnits','extraFeeFen','extraUnits','freeThresholdFen','regions','blockedRegions','productIds','enabled'}
    if set(body) != fields or body['mode'] not in ['FIXED','PIECE','WEIGHT']:
        raise ShippingRuleError('快递模板字段或计费方式无效。')
    return dict(name=name(body['name']),mode=body['mode'],firstFeeFen=integer(body['firstFeeFen']),
        firstUnits=integer(body['firstUnits'],1,1_000_000),extraFeeFen=integer(body['extraFeeFen']),
        extraUnits=integer(body['extraUnits'],1,1_000_000),freeThresholdFen=threshold(body['freeThresholdFen']),
        regions=regions(body['regions']),blockedRegions=regions(body['blockedRegions']),
        productIds=ids(body['productIds'],Product),enabled=enabled(body['enabled']))


def zone(body):
    fields = {'storeId','name','kind','center','radiusMeters','polygon','priority','feeMode','feeFen','firstMeters','extraMeters','extraFeeFen','minimumFen','freeThresholdFen','startTime','endTime','enabled'}
    if set(body) != fields or body['kind'] not in ['CIRCLE','POLYGON','EXCLUDE'] or body['feeMode'] not in ['FIXED','DISTANCE']:
        raise ShippingRuleError('围栏字段或收费方式无效。')
    store_id = identifier(body['storeId'])
    if not Store.objects.filter(pk=store_id).exists():
        raise ShippingRuleError('前置仓不存在。')
    polygon = body['polygon']
    if not isinstance(polygon,list) or len(polygon) > 100:
        raise ShippingRuleError('多边形最多 100 个顶点。')
    polygon = [point(item) for item in polygon]
    if body['kind'] != 'CIRCLE':
        if len(polygon) < 3 or len({(item['latitude'],item['longitude']) for item in polygon}) != len(polygon):
            raise ShippingRuleError('多边形至少需要 3 个不同的顶点。')
        area = sum(item['longitude']*polygon[index-1]['latitude']-polygon[index-1]['longitude']*item['latitude'] for index,item in enumerate(polygon))
        from .geometry import polygon_simple
        if not polygon_simple(polygon):
            raise ShippingRuleError('多边形边线不能交叉或重叠。')
        if abs(area) < 1e-12:
            raise ShippingRuleError('多边形顶点不能共线。')
    for key in ['startTime','endTime']:
        if not isinstance(body[key],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',body[key]):
            raise ShippingRuleError('配送时段格式须为 HH:mm。')
    return dict(storeId=store_id,name=name(body['name']),kind=body['kind'],
        center=point(body['center']) if body['kind']=='CIRCLE' else None,
        radiusMeters=integer(body['radiusMeters'],1,100000) if body['kind']=='CIRCLE' else None,
        polygon=[] if body['kind']=='CIRCLE' else polygon,priority=integer(body['priority'],0,9999),
        feeMode=body['feeMode'],feeFen=integer(body['feeFen']),firstMeters=integer(body['firstMeters'],0,100000),
        extraMeters=integer(body['extraMeters'],1,100000),extraFeeFen=integer(body['extraFeeFen']),
        minimumFen=integer(body['minimumFen'],0,1_000_000_000),freeThresholdFen=threshold(body['freeThresholdFen']),
        startTime=body['startTime'],endTime=body['endTime'],enabled=enabled(body['enabled']))


def campaign(body):
    if not isinstance(body,dict) or set(body) != {'enabled','name','startsAt','endsAt','minimumFen','storeIds','deliveryModes'}:
        raise ShippingRuleError('平台包邮活动字段无效。')
    start,end = parse_datetime(body['startsAt']),parse_datetime(body['endsAt'])
    if not start or not end or not start.tzinfo or not end.tzinfo or end <= start:
        raise ShippingRuleError('活动须填写带时区的有效起止时间。')
    modes=body['deliveryModes']
    if not isinstance(modes,list) or not modes or set(modes)-{'EXPRESS','DELIVERY'}:
        raise ShippingRuleError('活动交付方式无效。')
    return dict(enabled=enabled(body['enabled']),name=name(body['name']),startsAt=start.isoformat(),endsAt=end.isoformat(),
        minimumFen=integer(body['minimumFen'],0,1_000_000_000),storeIds=ids(body['storeIds'],Store),deliveryModes=sorted(set(modes)))


def weights(items):
    if not isinstance(items,list) or not 1 <= len(items) <= 200:
        raise ShippingRuleError('每次请保存 1 至 200 个规格重量。')
    result=[]
    for item in items:
        if not isinstance(item,dict) or set(item) != {'skuId','weightGrams','expectedUnitVersionId'}:
            raise ShippingRuleError('重量须关联当前销售单位版本。')
        sku_id=identifier(item['skuId']);version=identifier(item['expectedUnitVersionId'])
        sku=Sku.objects.filter(pk=sku_id).first()
        if not sku or str(sku.current_unit_id) != version:
            raise ShippingRuleError('商品销售单位已变化，请刷新后重新配置重量。')
        result.append(dict(skuId=sku_id,weightGrams=integer(item['weightGrams'],1,1_000_000),unitVersionId=version))
    if len({row['skuId'] for row in result}) != len(result):
        raise ShippingRuleError('规格不可重复。')
    return result
