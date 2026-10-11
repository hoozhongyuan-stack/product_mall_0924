"""Bounded atomic admin batches with live authority and optimistic revisions."""
from uuid import UUID
from django.db import transaction
from accounts.models import AdminAccount
from accounts.security import require,require_live,audit
from catalog.models import Product,Category
from catalog.presentation import category_data
from common.http import method,parse_json_object,response
from .access import DiningError,get_restaurant
from .models import Restaurant,ProductCommissionRule,DiningTable
from .service import restaurant_data,save_table,table_data
from .views import guarded,ratio_data


def batch_items(body,identity,value_key):
    if set(body)!={'items',value_key}:
        raise DiningError('批量请求字段不正确。')
    items=body['items']
    if not isinstance(items,list) or not 1<=len(items)<=100:
        raise DiningError('请选择 1 至 100 项。')
    normalized=[]
    for item in items:
        if not isinstance(item,dict) or set(item)!={identity,'expectedRevision'} or type(item.get('expectedRevision')) is not int or item['expectedRevision']<0 or not isinstance(item.get(identity),str):
            raise DiningError('选择项或修订号无效。')
        try:identifier=str(UUID(item[identity]))
        except ValueError:raise DiningError('选择项 ID 无效。') from None
        normalized.append((identifier,item['expectedRevision']))
    if len({row[0] for row in normalized})!=len(normalized):
        raise DiningError('不能重复选择。')
    return sorted(normalized)


@guarded
def batch_status(request):
    bad=method(request,'POST')
    if bad:return bad
    actor,bad=require(request,'dining.manage')
    if bad:return bad
    body=parse_json_object(request,max_bytes=32768)
    items=batch_items(body,'restaurantId','enabled')
    if type(body['enabled']) is not bool:raise DiningError('启用状态无效。')
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        rows={str(row.pk):row for row in Restaurant.objects.select_for_update(of=('self',)).select_related('assigned_store').filter(pk__in=[item[0] for item in items]).order_by('pk')}
        if len(rows)!=len(items):raise DiningError('餐饮店不存在。','DINING_NOT_FOUND',404)
        for identifier,revision in items:
            if rows[identifier].revision!=revision:raise DiningError('部分餐饮店已修改，请刷新后重试。','REVISION_CONFLICT',409)
        result=[]
        for identifier,revision in items:
            previous=rows[identifier]
            before={'enabled':previous.enabled,'revision':revision}
            Restaurant.objects.filter(pk=identifier).update(enabled=body['enabled'],revision=revision+1)
            previous.refresh_from_db()
            data=restaurant_data(previous)
            audit(request,'dining.restaurant.batch.status','restaurant',identifier,actor=actor,before=before,after=data)
            result.append(data)
    return response(request,{'items':result})


@guarded
def batch_ratios(request):
    bad=method(request,'POST')
    if bad:return bad
    actor,bad=require(request,'dining.manage')
    if bad:return bad
    body=parse_json_object(request,max_bytes=32768)
    items=batch_items(body,'productId','commissionBps')
    if type(body['commissionBps']) is not int or not 0<=body['commissionBps']<=10000:raise DiningError('佣金比例须在 0 至 100% 之间。')
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        products={str(row.pk):row for row in Product.objects.select_for_update().filter(pk__in=[item[0] for item in items],fulfillment_kind='SHIP').order_by('pk')}
        if len(products)!=len(items):raise DiningError('商品不存在或不支持餐饮联盟。','DINING_NOT_FOUND',404)
        rules={str(row.pk):row for row in ProductCommissionRule.objects.select_for_update().filter(pk__in=products).order_by('pk')}
        for identifier,revision in items:
            if (rules[identifier].revision if identifier in rules else 0)!=revision:raise DiningError('部分佣金比例已修改，请刷新后重试。','REVISION_CONFLICT',409)
        result=[]
        for identifier,revision in items:
            previous=rules.get(identifier)
            updated=ProductCommissionRule(product_id=identifier,commission_bps=body['commissionBps'],revision=revision+1)
            updated.save()
            data=ratio_data(products[identifier])
            audit(request,'dining.ratio.batch.configure','dining_ratio',identifier,actor=actor,before={'revision':revision,'commissionBps':previous.commission_bps if previous else None},after=data)
            result.append(data)
    return response(request,{'items':result})


@guarded
def categories(request):
    bad=method(request,'GET')
    if bad:return bad
    _,bad=require(request,'dining.read')
    if bad:return bad
    rows=Category.objects.select_related('image','banner').order_by('sort_order','name','id')
    return response(request,{'items':[category_data(row) for row in rows]})


@guarded
def batch_tables(request,restaurant_id):
    bad=method(request,'POST')
    if bad:return bad
    actor,bad=require(request,'dining.manage')
    if bad:return bad
    body=parse_json_object(request,max_bytes=32768)
    numbers=body.get('numbers')
    if set(body)!={'numbers','enabled'} or type(body.get('enabled')) is not bool or not isinstance(numbers,list) or not 1<=len(numbers)<=100 or any(not isinstance(number,str) or not 1<=len(number.strip())<=40 for number in numbers):
        raise DiningError('请输入 1 至 100 个桌号，每个桌号为 1 至 40 个字符。')
    numbers=[number.strip() for number in numbers]
    if len(set(numbers))!=len(numbers):raise DiningError('批量桌号不能重复。')
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        get_restaurant(restaurant_id,lock=True)
        if DiningTable.objects.filter(restaurant_id=restaurant_id,number__in=numbers).exists():raise DiningError('部分桌号已存在，请修改后重试。','DINING_TABLE_DUPLICATE',409)
        result=[]
        for number in numbers:
            row=save_table(restaurant_id,{'number':number,'enabled':body['enabled']})
            data=table_data(row)
            audit(request,'dining.table.batch.create','dining_table',row.id,actor=actor,after=data)
            result.append(data)
    return response(request,{'items':result},201)
