"""Revisioned administrative configuration endpoints."""
from functools import wraps
from uuid import uuid4
from django.db import transaction
from common.http import method, response, error, parse_json_object
from accounts.security import require, require_live, audit
from . import configuration as validate
from .models import ShippingPolicy
from .rules import ShippingRuleError


def guarded(function):
    @wraps(function)
    def inner(request,*args,**kwargs):
        try:
            denied=method(request,'GET','POST','PUT','PATCH','DELETE')
            if denied:return denied
            from stores.quota import allowed
            if not allowed(request,'shipping.'+function.__name__):
                return error(request,429,'RATE_LIMITED','操作过于频繁，请稍后再试。')
            actor,denied=require(request,'settlement.shipping.manage')
            if denied:return denied
            return function(request,actor,*args,**kwargs)
        except ShippingRuleError as exc:
            return error(request,400,'VALIDATION_FAILED',str(exc))
        except (ValueError,TypeError,KeyError):
            return error(request,400,'VALIDATION_FAILED','配送规则参数无效。')
    return inner


def data(policy):
    return dict(revision=policy.revision,templates=policy.templates,zones=policy.zones,
                campaign=policy.campaign,skuWeights=policy.sku_weights)


def mutate(request, actor, action, transform):
    body=parse_json_object(request)
    expected=body.pop('expectedRevision',None)
    if type(expected) is not int or expected < 1:
        raise ShippingRuleError('请提交当前配置修订号。')
    with transaction.atomic():
        policy=ShippingPolicy.objects.select_for_update().get(pk=1)
        actor,denied=require_live(request,'settlement.shipping.manage')
        if denied:return denied
        if expected != policy.revision:
            return error(request,409,'REVISION_CONFLICT','配置已变化，请刷新后重试。',[{'currentRevision':policy.revision}])
        before=data(policy)
        changes=transform(policy,body)
        for key,value in changes.items():setattr(policy,key,value)
        if before==data(policy):return response(request,data(policy))
        policy.revision+=1
        policy.save(update_fields=[*changes,'revision','updated_at'])
        audit(request,action,'shipping_policy',1,actor,before=before,after=data(policy))
        return response(request,data(policy))


@guarded
def config_view(request,actor):
    denied=method(request,'GET','PATCH')
    if denied:return denied
    if request.method=='GET':return response(request,data(ShippingPolicy.objects.get(pk=1)))
    def update(policy,body):
        if set(body) != {'campaign'}:raise ShippingRuleError('配置字段无效。')
        return {'campaign':validate.campaign(body['campaign'])}
    return mutate(request,actor,'shipping.campaign.update',update)


def resource_view(request,actor,identifier,resource,validator,maximum):
    denied=method(request,*(['PUT','DELETE'] if identifier else ['POST']))
    if denied:return denied
    proposed=validator({key:value for key,value in parse_json_object(request).items() if key!='expectedRevision'}) if request.method!='DELETE' else None
    def update(policy,body):
        rows=getattr(policy,resource)
        current=next((row for row in rows if row['id']==str(identifier)),None) if identifier else None
        if identifier and not current:raise ShippingRuleError('规则不存在，请刷新。')
        if request.method=='DELETE':
            if body:raise ShippingRuleError('删除字段无效。')
            return {resource:[row for row in rows if row['id']!=str(identifier)]}
        record={**proposed,'id':str(identifier or uuid4())}
        others=[row for row in rows if row['id']!=record['id']]
        if len(others)>=maximum:raise ShippingRuleError('规则数量超过上限。')
        if resource=='templates' and record['enabled']:
            for row in others:
                if not row['enabled']:continue
                if not row['productIds'] and not record['productIds']:
                    raise ShippingRuleError('只能启用一个默认快递模板。')
                if set(row['productIds']) & set(record['productIds']):
                    raise ShippingRuleError('同一商品不可关联多个已启用模板。')
        return {resource:[*others,record]}
    return mutate(request,actor,'shipping.'+resource+'.'+request.method.lower(),update)


@guarded
def templates_view(request,actor,identifier=None):
    return resource_view(request,actor,identifier,'templates',validate.template,100)


@guarded
def zones_view(request,actor,identifier=None):
    return resource_view(request,actor,identifier,'zones',validate.zone,200)


@guarded
def bulk_view(request,actor):
    denied=method(request,'POST')
    if denied:return denied
    def update(policy,body):
        if set(body)!={'resource','ids','enabled'} or body['resource'] not in ('templates','zones'):
            raise ShippingRuleError('批量操作字段无效。')
        enabled=validate.enabled(body['enabled']);ids=body['ids']
        if not isinstance(ids,list) or not 1 <=len(ids)<=100:
            raise ShippingRuleError('请选择 1 至 100 条规则。')
        ids={validate.identifier(item) for item in ids}
        rows=getattr(policy,body['resource'])
        if not ids.issubset({row['id'] for row in rows}):raise ShippingRuleError('部分规则不存在。')
        result=[{**row,'enabled':enabled} if row['id'] in ids else row for row in rows]
        if body['resource']=='templates':
            active=[row for row in result if row['enabled']]
            if sum(not row['productIds'] for row in active)>1:raise ShippingRuleError('只能启用一个默认快递模板。')
            products=[product for row in active for product in row['productIds']]
            if len(set(products))!=len(products):raise ShippingRuleError('商品关联模板重复。')
        return {body['resource']:result}
    return mutate(request,actor,'shipping.bulk',update)


@guarded
def weights_view(request,actor):
    denied=method(request,'PUT')
    if denied:return denied
    def update(policy,body):
        if set(body)!={'items'}:raise ShippingRuleError('重量字段无效。')
        rows=validate.weights(body['items']);changed={row['skuId'] for row in rows}
        result=[row for row in policy.sku_weights if row['skuId'] not in changed]+rows
        if len(result)>1000:raise ShippingRuleError('最多配置 1000 个规格重量。')
        return {'sku_weights':sorted(result,key=lambda row:row['skuId'])}
    return mutate(request,actor,'shipping.weights.update',update)


@guarded
def trial_view(request,actor):
    denied=method(request,'POST')
    if denied:return denied
    from .service import quote_shipping
    from stores.models import Store
    body=parse_json_object(request)
    if set(body)!={'storeId','deliveryMode','goodsFen','couponDiscountFen','items','address'}:
        raise ShippingRuleError('试算字段无效。')
    amount=validate.integer(body['goodsFen'],0,1_000_000_000)
    coupon=validate.integer(body['couponDiscountFen'],0,amount)
    items=body['items']
    if not isinstance(items,list) or not 1<=len(items)<=50:raise ShippingRuleError('请选择试算商品。')
    from catalog.quote_access import quote_catalog_rows
    ids=[validate.identifier(item['skuId']) for item in items]
    rows=quote_catalog_rows(ids)
    lines=[]
    for item in items:
        identifier=validate.identifier(item['skuId'])
        from uuid import UUID
        sku=rows.get(UUID(identifier))
        if not sku or not sku['productId']:raise ShippingRuleError('试算商品不可用。')
        lines.append(dict(skuId=identifier,productId=sku['productId'],fulfillmentKind='SHIP',quantity=validate.integer(item['quantity'],1,9999)))
    store=Store.objects.select_related('warehouse').filter(pk=validate.identifier(body['storeId'])).first() if body['storeId'] else None
    if body['storeId'] and not store:raise ShippingRuleError('前置仓不存在。')
    if body['deliveryMode'] not in ['DELIVERY','EXPRESS','PICKUP']:raise ShippingRuleError('交付方式无效。')
    address=body['address']
    if not isinstance(address,dict) or set(address)-{'province','city','district','latitude','longitude'}:
        raise ShippingRuleError('地址字段无效。')
    for key in ('province','city','district'):
        if key in address and (not isinstance(address[key],str) or len(address[key])>80):raise ShippingRuleError('地区名称无效。')
    if address.get('latitude') is not None:validate.point({key:address.get(key) for key in ('latitude','longitude')})
    return response(request,quote_shipping(lines,store,body['deliveryMode'],address,amount-coupon))


@guarded
def regions_view(request,actor):
    denied=method(request,'GET')
    if denied:return denied
    from .regions import region_children, RegionUnavailable
    try:return response(request,{'items':region_children(request.GET)})
    except RegionUnavailable as exc:return error(request,503,'REGIONS_UNAVAILABLE',str(exc))


@guarded
def targets_view(request,actor):
    denied=method(request,'GET')
    if denied:return denied
    from catalog.shipping_access import shipping_targets
    page=validate.integer(int(request.GET.get('page','1')),1,10000)
    size=validate.integer(int(request.GET.get('pageSize','20')),1,50)
    query=request.GET.get('search','').strip()
    if len(query)>120:raise ShippingRuleError('检索关键词过长。')
    return response(request,shipping_targets(query,page,size,ShippingPolicy.objects.get(pk=1).sku_weights))


@guarded
def stores_view(request,actor,identifier=None):
    denied=method(request,'GET')
    if denied:return denied
    from stores.shipping_access import shipping_store_rows
    page=validate.integer(int(request.GET.get('page','1')),1,10000)
    size=validate.integer(int(request.GET.get('pageSize','20')),1,200)
    query=request.GET.get('search','').strip()
    if len(query)>120:raise ShippingRuleError('检索关键词过长。')
    result=shipping_store_rows(query,page,size,identifier)
    if identifier:
        if not result['items']:return error(request,404,'STORE_NOT_FOUND','前置仓不存在。')
        return response(request,result['items'][0])
    return response(request,result)
