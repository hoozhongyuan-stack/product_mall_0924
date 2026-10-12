"""Validated administration and dining catalogue read models."""
import re
import uuid
import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from catalog.models import Product, Sku, SkuGradePrice
from stores.models import Store
from stores.service import distance_meters
from .access import DiningError, get_restaurant, checkout_context
from .models import Restaurant, RestaurantProduct, DiningTable, DiningSession

FIELD_MAP = {'name':'name','address':'address','contactName':'contact_name','contactPhone':'contact_phone','latitude':'latitude','longitude':'longitude','enabled':'enabled'}


def restaurant_data(row):
    return {'id':str(row.id), **{key:float(getattr(row,value)) if key in ('latitude','longitude') else getattr(row,value) for key,value in FIELD_MAP.items()}, 'fulfillmentStore':{'id':str(row.assigned_store_id), 'name':row.assigned_store.name}, 'enabled':row.enabled, 'revision':row.revision, 'tableCount':row.tables.count()}


def nearest_store(latitude, longitude):
    ranked = []
    for store in Store.objects.select_related('warehouse').filter(enabled=True, accepting_orders=True, warehouse__enabled=True, latitude__isnull=False, longitude__isnull=False):
        if 'DELIVERY' not in store.supported_modes:
            continue
        distance = distance_meters(float(latitude),float(longitude),float(store.latitude),float(store.longitude))
        from shipping.service import delivery_point_eligibility
        if not delivery_point_eligibility(store, {'latitude':float(latitude),'longitude':float(longitude)}, check_time=False):
            ranked.append((distance, str(store.id), store))
    if not ranked:
        raise DiningError('餐饮店地址附近没有支持配送的前置仓，请先配置前置仓坐标和配送范围。', 'DINING_NO_WAREHOUSE', 409)
    return min(ranked, key=lambda item:(item[0],item[1]))[2]


def validated_fields(body, update=False):
    if set(body)-set(FIELD_MAP)-({'revision'} if update else {'requestKey'}) or not update and not {'name','address','contactName','contactPhone','latitude','longitude'}.issubset(body):
        raise DiningError('请填写餐饮店名称、地址、联系人、电话和经纬度。')
    fields = {}
    for key,value in body.items():
        if key in ('revision','requestKey'):
            continue
        if key=='enabled':
            if type(value) is not bool:
                raise DiningError('启用状态无效。')
        elif key in ('latitude','longitude'):
            try:
                value = Decimal(str(value))
                if not value.is_finite() or abs(value)>(90 if key=='latitude' else 180):
                    raise ValueError()
            except (ValueError,InvalidOperation):
                raise DiningError('经纬度无效。')
        else:
            if not isinstance(value,str) or not value.strip() or len(value.strip())>Restaurant._meta.get_field(FIELD_MAP[key]).max_length:
                raise DiningError('餐饮店文字字段长度或格式无效。')
            value=value.strip()
            if key=='contactPhone' and not re.fullmatch(r'[+\d][\d ()-]{4,29}',value):
                raise DiningError('联系电话无效。')
        fields[FIELD_MAP[key]]=value
    return fields


@transaction.atomic
def save_restaurant(body, restaurant_id=None):
    fields=validated_fields(body, bool(restaurant_id))
    if restaurant_id:
        row=get_restaurant(restaurant_id, lock=True)
        if type(body.get('revision')) is not int or body['revision']!=row.revision:
            raise DiningError('餐饮店已修改，请刷新。','REVISION_CONFLICT',409)
        for key,value in fields.items():
            setattr(row,key,value)
        row.revision+=1
        if {'address','latitude','longitude'} & set(fields):
            row.assigned_store=nearest_store(row.latitude,row.longitude)
    else:
        try:
            key=uuid.UUID(body.get('requestKey',''))
        except (ValueError,TypeError,AttributeError):
            raise DiningError('请提供有效的创建请求编号。') from None
        digest=hashlib.sha256(json.dumps(fields,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()
        from django.db import connection
        if connection.vendor=='postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)',[int.from_bytes(key.bytes[:8],'big',signed=True)])
        prior=Restaurant.objects.filter(creation_request_key=key).first()
        if prior:
            if prior.creation_digest!=digest:
                raise DiningError('创建请求编号已用于不同餐饮店资料。','IDEMPOTENCY_CONFLICT',409)
            return prior
        row=Restaurant(**fields, creation_request_key=key, creation_digest=digest, assigned_store=nearest_store(fields['latitude'],fields['longitude']))
    row.save()
    return row


def table_data(row):
    return {'id':str(row.id),'number':row.number,'enabled':row.enabled,'revision':row.revision,'tableCode':row.code,'codePath':f'/api/v1/admin/dining-alliance/restaurants/{row.restaurant_id}/tables/{row.id}/code'}


@transaction.atomic
def save_table(restaurant_id, body, table_id=None):
    get_restaurant(restaurant_id, lock=True)
    allowed={'number','enabled','revision'} if table_id else {'number','enabled'}
    if set(body)-allowed or not table_id and 'number' not in body:
        raise DiningError('餐桌字段无效。')
    row=DiningTable.objects.select_for_update().filter(pk=table_id,restaurant_id=restaurant_id).first() if table_id else DiningTable(restaurant_id=restaurant_id)
    if row is None:
        raise DiningError('餐桌不存在。','DINING_NOT_FOUND',404)
    if table_id and (type(body.get('revision')) is not int or body['revision']!=row.revision):
        raise DiningError('餐桌已修改，请刷新。','REVISION_CONFLICT',409)
    if 'number' in body:
        number=body['number']
        if not isinstance(number,str) or not number.strip() or len(number.strip())>40:
            raise DiningError('桌号应为 1 至 40 个字符。')
        if DiningTable.objects.filter(restaurant_id=restaurant_id,number=number.strip()).exclude(pk=row.pk).exists():
            raise DiningError('本店已有该桌号。','DINING_TABLE_DUPLICATE',409)
        row.number=number.strip()
    if 'enabled' in body:
        if type(body['enabled']) is not bool:
            raise DiningError('餐桌启用状态无效。')
        row.enabled=body['enabled']
    row.revision+=1 if table_id else 0
    row.save()
    return row


@transaction.atomic
def scan(member, code):
    if not isinstance(code,str) or not re.fullmatch('[0-9a-f]{32}',code):
        raise DiningError('餐桌码无效，请重新扫码。')
    from customers.models import Member
    live=Member.objects.select_for_update().filter(pk=member.pk,enabled=True,auth_version=member.auth_version).first()
    if live is None:
        raise DiningError('会员登录已失效。','DINING_FORBIDDEN',403)
    table=DiningTable.objects.filter(code=code,enabled=True).first()
    if table is None:
        raise DiningError('餐桌码不可用。','DINING_NOT_FOUND',404)
    restaurant=get_restaurant(table.restaurant_id,lock=True)
    # Each fresh scan closes prior sessions; never restore a shop from history.
    DiningSession.objects.filter(member=member,closed_at__isnull=True).update(closed_at=timezone.now())
    row=DiningSession.objects.create(member=member,table=table,store=restaurant.assigned_store,member_auth_version=member.auth_version,restaurant_revision=restaurant.revision,table_revision=table.revision,store_revision=restaurant.assigned_store.revision,expires_at=timezone.now()+timedelta(hours=2))
    store,context=checkout_context(row.id,member)
    return {'sessionId':str(row.id),'expiresAt':row.expires_at.isoformat(),'restaurant':restaurant_data(restaurant),'table':{'id':str(table.id),'number':table.number},'fulfillmentStore':{'id':str(store.id),'name':store.name}}


def product_queryset(restaurant, customer=False):
    from stores.access import sale_product_ids as warehouse_ids
    rows=Product.objects.filter(fulfillment_kind='SHIP').exclude(status='DRAFT')
    if customer:
        dining_ids=RestaurantProduct.objects.filter(restaurant=restaurant,on_sale=True).values('product_id')
        rows=rows.filter(pk__in=dining_ids,status='ON_SALE',category__status='ACTIVE',category__parent__status='ACTIVE',skus__sale_status='ON_SALE',skus__current_unit__isnull=False).filter(pk__in=warehouse_ids(restaurant.assigned_store)).distinct()
    return rows.order_by('name','id')


def products_data(restaurant, member=None, offset=0, limit=100, customer=False):
    from django.db.models import Prefetch
    from catalog.spec_read import specification_prefetch, specification_label
    from inventory.store_access import available_base_units
    from inventory.pool_access import resolve_anchor_ids
    from stores.access import sale_product_ids as warehouse_ids
    sku_rows=Sku.objects.select_related('current_unit').prefetch_related(specification_prefetch())
    rows=list(product_queryset(restaurant,customer).prefetch_related(Prefetch('skus',queryset=sku_rows))[offset:offset+limit])
    listings={row.product_id:row for row in RestaurantProduct.objects.filter(restaurant=restaurant,product__in=rows)}
    skus=[sku for product in rows for sku in product.skus.all()]
    stock=available_base_units(restaurant.assigned_store,[sku.id for sku in skus])
    anchors=resolve_anchor_ids([sku.id for sku in skus])
    prices={row.sku_id:row.price_fen for row in SkuGradePrice.objects.filter(sku__in=skus,grade=member.grade,active=True)} if member else {}
    selling=set(warehouse_ids(restaurant.assigned_store))
    result=[]
    for product in rows:
        listing=listings.get(product.id)
        sku_data=[]
        for sku in product.skus.all():
            unit=sku.current_unit
            if customer and (sku.sale_status!='ON_SALE' or unit is None):
                continue
            available=stock.get(sku.id,0)
            sku_data.append({'id':str(sku.id),'skuCode':sku.sku_code,'specKey':sku.spec_key,'specLabel':specification_label(sku),'specs':specification_label(sku),'platformStatus':sku.sale_status,'anchorSkuId':str(anchors[sku.id]),'baseUnit':unit.base_unit if unit else '', 'saleUnit':unit.sale_unit if unit else '', 'ratio':unit.ratio if unit else None,'availableBaseUnits':available,'availableQuantity':available//unit.ratio if unit else 0,'unitPriceFen':prices.get(sku.pk,sku.list_price_fen)})
        image=f'/api/v1/app/assets/{product.main_image_id}/file' if product.main_image_id and product.status=='ON_SALE' else None
        revision=listing.revision if listing else 0
        result.append({'id':str(product.id),'name':product.name,'mainImageUrl':image,'platformStatus':product.status,'onSale':bool(listing and listing.on_sale),'revision':revision,'listingRevision':revision,'warehouseOnSale':product.id in selling,'skus':sku_data})
    return result


@transaction.atomic
def update_product(restaurant, body):
    get_restaurant(restaurant.id,lock=True)
    if set(body)!={'productId','onSale','revision'} or type(body['onSale']) is not bool or type(body['revision']) is not int:
        raise DiningError('商品上下架字段无效。')
    product=Product.objects.filter(pk=body['productId'],fulfillment_kind='SHIP').exclude(status='DRAFT').first()
    if not product:
        raise DiningError('商品不存在。','DINING_NOT_FOUND',404)
    if body['onSale'] and product.status!='ON_SALE':
        raise DiningError('平台商品已下架。','DINING_PRODUCT_UNAVAILABLE',409)
    row,created=RestaurantProduct.objects.get_or_create(restaurant=restaurant,product=product)
    if body['revision']!=(0 if created else row.revision):
        raise DiningError('商品上下架已变化，请刷新。','REVISION_CONFLICT',409)
    row.on_sale=body['onSale'];row.revision+=1 if not created else 0;row.save()
    return {'id':str(product.id),'name':product.name,'onSale':row.on_sale,'revision':row.revision,'listingRevision':row.revision}


from .finance import snapshot_order
