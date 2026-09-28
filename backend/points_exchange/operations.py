"""Admin offer operations and privacy-safe bounded catalog reads."""
from django.db import transaction,connection
from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from accounts.security import require_live,permissions,confirm_action,audit
from catalog.exchange_access import exchange_catalog_rows,exchange_sku_options,eligible_exchange_sku_ids
from inventory.availability import default_available_base_units
from orders.service import OrderError
from .models import ExchangeOffer,ExchangeOperation
from .orders import digest,identity


PERMISSIONS={'create':'exchange.manage','edit':'exchange.manage','availability':'exchange.publish'}


def offer_rows(offers):
    offers=list(offers);rows=exchange_catalog_rows([offer.sku_id for offer in offers])
    warehouse,balances=default_available_base_units([offer.sku_id for offer in offers])
    return [{'id':str(offer.id),'pointsPrice':offer.points_price,'status':offer.status,'revision':offer.revision,
             'createdAt':offer.created_at.isoformat(),
             **{key:row[key] for key in ['skuId','productId','productName','skuCode','specs','saleUnit','fulfillmentKind','redeemValidUntil','imageUrl']},
             'availableQuantity':balances.get(offer.sku_id,0)//row['ratio'] if row['ratio'] and warehouse else 0,
             'catalogOnSale':row['eligible']} for offer in offers for row in [rows[offer.sku_id]]]


def page(params,allowed=()):
    if set(params)-set(allowed)-{'page','pageSize'} or any(len(value)!=1 for _,value in params.lists()):
        raise OrderError('查询字段不正确或重复。')
    try: index=int(params.get('page','1'));size=int(params.get('pageSize','20'))
    except ValueError: raise OrderError('分页格式不正确。')
    if not 1<=index<=10000 or not 1<=size<=100: raise OrderError('分页超出范围。')
    search=params.get('q','').strip()
    if len(search)>100: raise OrderError('查询关键词过长。')
    return index,size,search


def list_offers(params,*,public=False):
    index,size,search=page(params,('q',) if public else ('q','status'))
    query=ExchangeOffer.objects.all()
    if public: query=query.filter(status='ON_SALE',sku_id__in=eligible_exchange_sku_ids())
    elif params.get('status'):
        if params['status'] not in {'DRAFT','ON_SALE','OFF_SALE'}: raise OrderError('积分商品状态不正确。')
        query=query.filter(status=params['status'])
    if search: query=query.filter(Q(sku__product__name__icontains=search)|Q(sku__sku_code__icontains=search))
    total=query.count()
    return {'items':offer_rows(query.order_by('-created_at','-id')[(index-1)*size:index*size]),
            'pagination':{'page':index,'pageSize':size,'total':total}}


def sku_options(params):
    index,size,search=page(params,('q',));items,total=exchange_sku_options(search,index,size)
    offers=dict(ExchangeOffer.objects.filter(sku_id__in=[row['skuId'] for row in items]).values_list('sku_id','id'))
    return {'items':[{**row,'existingOfferId':str(offers[identity(row['skuId'],'SKU')]) if identity(row['skuId'],'SKU') in offers else None} for row in items],
            'pagination':{'page':index,'pageSize':size,'total':total}}


def _price(value):
    if type(value) is not int or not 1<=value<=1000000: raise OrderError('积分单价须为 1 至 1000000 的整数。')
    return value


def _permit(request,action):
    actor,bad=require_live(request,'exchange.read')
    if bad: return None,bad
    actor,bad=require_live(request,PERMISSIONS[action])
    return actor,bad


@transaction.atomic
def operate(request,action,body,key,offer_id=None):
    allowed={'create':{'skuId','pointsPrice'},'edit':{'expectedRevision','pointsPrice'},'availability':{'expectedRevision','status'}}[action]
    if set(body)!=allowed: raise OrderError('积分商品字段不完整或包含未知字段。')
    key=identity(key,'请求标识');actor,bad=_permit(request,action)
    if bad:return None,bad,False
    material={'action':action,'offerId':str(offer_id) if offer_id else None,'body':body};request_digest=digest(material)
    lock_key=int.from_bytes(bytes.fromhex(digest([str(actor.id),str(key)]))[:8],'big',signed=True)
    with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_xact_lock(%s)',[lock_key])
    actor,bad=_permit(request,action)
    if bad:return None,bad,False
    prior=ExchangeOperation.objects.filter(actor=actor,key=key).first()
    if prior:
        if prior.digest!=request_digest: raise OrderError('请求标识已用于不同操作。','IDEMPOTENCY_CONFLICT',409)
        return prior.result,None,True
    if ExchangeOperation.objects.filter(actor=actor,created_at__gte=timezone.now()-timedelta(minutes=15)).count()>=60:
        raise OrderError('操作过多，请稍后重试。','RATE_LIMITED',429)
    if action=='create':
        sku_id=identity(body['skuId'],'SKU');row=exchange_catalog_rows([sku_id]).get(sku_id)
        if not row: raise OrderError('SKU不存在。','SKU_NOT_FOUND',404)
        if ExchangeOffer.objects.filter(sku_id=sku_id).exists(): raise OrderError('该SKU已配置积分商品。','EXCHANGE_OFFER_EXISTS',409)
        exchange_catalog_rows([sku_id],lock=True)
        actor,bad=_permit(request,action)
        if bad:return None,bad,False
        offer=ExchangeOffer.objects.create(sku_id=sku_id,points_price=_price(body['pointsPrice']))
        before={}
    else:
        identity_row=ExchangeOffer.objects.filter(pk=offer_id).values_list('sku_id',flat=True).first()
        if identity_row:exchange_catalog_rows([identity_row],lock=True)
        offer=ExchangeOffer.objects.select_for_update().filter(pk=offer_id).first()
        if not offer: raise OrderError('积分商品不存在。','EXCHANGE_OFFER_NOT_FOUND',404)
        actor,bad=_permit(request,action)
        if bad:return None,bad,False
        if type(body['expectedRevision']) is not int or body['expectedRevision']!=offer.revision:
            raise OrderError('积分商品已变化，请刷新。','REVISION_CONFLICT',409)
        before=offer_rows([offer])[0]
        if action=='edit': offer.points_price=_price(body['pointsPrice'])
        else:
            if body['status'] not in {'ON_SALE','OFF_SALE'}: raise OrderError('请选择上架或下架。')
            row=exchange_catalog_rows([offer.sku_id])[offer.sku_id]
            if body['status']=='ON_SALE' and not row['eligible']: raise OrderError('商品内容、分类、单位或核销日期不可售。','CATALOG_UNAVAILABLE',409)
            bad=confirm_action(request,actor,'exchange.publish',offer.id,offer.revision)
            if bad:return None,bad,False
            offer.status=body['status']
        offer.revision+=1;offer.save(update_fields=['points_price','status','revision'])
    result=offer_rows([offer])[0]
    ExchangeOperation.objects.create(actor=actor,key=key,action=action,offer=offer,digest=request_digest,result=result)
    audit(request,'exchange.'+action,'exchange_offer',offer.id,actor,before=before,after=result)
    return result,None,False


def operation_result(request,key):
    actor,bad=require_live(request,'exchange.read')
    if bad:return None,bad
    prior=ExchangeOperation.objects.filter(actor=actor,key=key).first()
    if prior:
        actor,bad=_permit(request,prior.action)
        if bad:return None,bad
    return ({'status':'COMPLETED','result':prior.result} if prior else {'status':'NOT_FOUND'}),None
