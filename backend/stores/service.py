"""Store management DTOs and validated writes."""
import math
import re
from decimal import Decimal, InvalidOperation
from django.db import transaction
from catalog.models import Product
from inventory.models import Warehouse, InventoryBalance
from inventory.pool_access import resolve_anchor_ids
from inventory.store_access import available_base_units, set_available_stock
from .access import StoreError
from .models import Store, StoreProduct

FIELD_MAP = {'name':'name','contactName':'contact_name','contactPhone':'contact_phone','address':'address','city':'city','latitude':'latitude','longitude':'longitude','openingHours':'opening_hours','enabled':'enabled','acceptingOrders':'accepting_orders','supportedModes':'supported_modes','deliveryRadiusMeters':'delivery_radius_meters','deliveryFeeFen':'delivery_fee_fen'}


def store_data(store,admin=False,latitude=None,longitude=None):
    result = {'id':str(store.id),**{key:getattr(store,value) for key,value in FIELD_MAP.items()},'revision':store.revision}
    for key in ('latitude','longitude'):
        result[key] = float(result[key]) if result[key] is not None else None
    if admin:
        result['warehouseId'] = str(store.warehouse_id)
    if latitude is not None and store.latitude is not None:
        result['distanceMeters'] = round(distance_meters(latitude,longitude,float(store.latitude),float(store.longitude)))
    return result


def distance_meters(lat1,lng1,lat2,lng2):
    lat1,lat2 = math.radians(lat1),math.radians(lat2)
    delta_lat = lat2-lat1
    delta_lng = math.radians(lng2-lng1)
    return 6371000*2*math.asin(min(1,math.sqrt(math.sin(delta_lat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(delta_lng/2)**2)))


def validated_fields(body,update=False):
    if set(body)-set(FIELD_MAP)-({'revision'} if update else set()):
        raise StoreError('包含未支持的门店字段。')
    if not update and not {'name','contactName','contactPhone','address'}.issubset(body):
        raise StoreError('请填写门店名称、联系人、联系电话和地址。')
    result = {}
    for key,value in body.items():
        if key == 'revision':
            continue
        if key in ('enabled','acceptingOrders'):
            if type(value) is not bool:
                raise StoreError(f'{key} 必须为布尔值。')
        elif key == 'supportedModes':
            if not isinstance(value,list) or not value or len(value)>3 or any(not isinstance(item,str) or item not in ('PICKUP','DELIVERY','EXPRESS') for item in value) or len(set(value))!=len(value):
                raise StoreError('请选择有效的履约方式。')
        elif key in ('deliveryRadiusMeters','deliveryFeeFen'):
            if value is not None and (type(value) is not int or value<0 or value>1000000):
                raise StoreError('配送范围和配送费须为非负整数。')
        elif key in ('latitude','longitude'):
            if value is not None:
                try:
                    value = Decimal(str(value))
                    limit = 90 if key=='latitude' else 180
                    if not value.is_finite() or abs(value)>limit:
                        raise ValueError()
                except (InvalidOperation,ValueError):
                    raise StoreError('门店经纬度无效。')
        else:
            limit = Store._meta.get_field(FIELD_MAP[key]).max_length
            if not isinstance(value,str) or len(value.strip())>limit or (key!='city' and not value.strip()):
                raise StoreError(f'{key} 长度或格式无效。')
            value = value.strip()
            if key=='contactPhone' and not re.fullmatch(r'[+\d][\d ()-]{4,29}',value):
                raise StoreError('请填写有效的联系电话。')
        result[FIELD_MAP[key]] = value
    return result


def save_store(body,store=None):
    fields = validated_fields(body,store is not None)
    with transaction.atomic():
        if store is None:
            import uuid
            code = 'STORE-'+uuid.uuid4().hex
            warehouse = Warehouse.objects.create(code=code,name=fields['name'])
            store = Store(warehouse=warehouse,**{'supported_modes':['PICKUP','EXPRESS'],**fields})
        else:
            store = Store.objects.select_for_update().get(pk=store.pk)
            if type(body.get('revision')) is not int or body['revision']!=store.revision:
                raise StoreError('门店已被修改，请刷新后重试。','REVISION_CONFLICT',409)
            for field,value in fields.items():
                setattr(store,field,value)
            store.revision += 1
        if (store.latitude is None)!=(store.longitude is None):
            raise StoreError('经纬度须同时填写。')
        store.save()
        return store


def products_data(store,product_id=None,offset=0,limit=100,query=''):
    queryset=Product.objects.filter(fulfillment_kind='SHIP').exclude(status='DRAFT')
    if product_id is not None:queryset=queryset.filter(pk=product_id)
    if query:queryset=queryset.filter(name__icontains=query[:120])
    products=list(queryset.prefetch_related('skus__current_unit').order_by('name','id')[offset:offset+limit])
    listings = {row.product_id:row for row in StoreProduct.objects.filter(store=store)}
    skus = [sku for product in products for sku in product.skus.all()]
    anchors = resolve_anchor_ids([sku.id for sku in skus])
    balances = {row.sku_id:row for row in InventoryBalance.objects.filter(warehouse_id=store.warehouse_id,sku_id__in=set(anchors.values()))}
    result = []
    for product in products:
        listing = listings.get(product.id)
        rows = []
        for sku in product.skus.all():
            unit,anchor = sku.current_unit,anchors[sku.id]
            balance = balances.get(anchor)
            rows.append({'id':str(sku.id),'skuCode':sku.sku_code,'specKey':sku.spec_key,'platformStatus':sku.sale_status,'anchorSkuId':str(anchor),'baseUnit':unit.base_unit if unit else '', 'saleUnit':unit.sale_unit if unit else '', 'ratio':unit.ratio if unit else None,'availableBaseUnits':balance.on_hand_base_units-balance.reserved_base_units if balance else 0,'reservedBaseUnits':balance.reserved_base_units if balance else 0})
        result.append({'id':str(product.id),'name':product.name,'platformStatus':product.status,'onSale':bool(listing and listing.on_sale),'revision':listing.revision if listing else 0,'skus':rows})
    return result


def update_product(store,product_id,body,member):
    if set(body)-{'revision','onSale','stock'} or type(body.get('revision')) is not int:
        raise StoreError('商品设置字段无效。')
    if 'onSale' in body and type(body['onSale']) is not bool:
        raise StoreError('上下架状态须为布尔值。')
    stock = body.get('stock',[])
    if not isinstance(stock,list) or len(stock)>50:
        raise StoreError('库存明细无效。')
    with transaction.atomic():
        Store.objects.select_for_update().get(pk=store.pk)
        product = Product.objects.filter(pk=product_id,fulfillment_kind='SHIP').exclude(status='DRAFT').first()
        if not product:
            raise StoreError('商品不存在。','PRODUCT_NOT_FOUND',404)
        listing,created = StoreProduct.objects.get_or_create(store=store,product=product)
        if body['revision']!=(0 if created else listing.revision):
            raise StoreError('商品已被修改，请刷新后重试。','REVISION_CONFLICT',409)
        seen = set()
        for row in stock:
            if not isinstance(row,dict) or set(row)!={'skuId','availableBaseUnits','expectedAvailableBaseUnits'} or not product.skus.filter(pk=row['skuId']).exists():
                raise StoreError('库存规格不属于该商品。')
            from inventory.pool_access import resolve_anchor_id
            anchor = resolve_anchor_id(product.skus.get(pk=row['skuId']).id)
            if anchor in seen:
                raise StoreError('同一库存池只需填写一次。')
            seen.add(anchor)
            set_available_stock(store,row['skuId'],row['availableBaseUnits'],row['expectedAvailableBaseUnits'],member)
        if 'onSale' in body:
            listing.on_sale = body['onSale']
        listing.revision += 1
        listing.save()
    return next(row for row in products_data(store,product_id=product.id) if row['id']==str(product.id))


def account_data(store):
    from payments.store_accounts import account_data as read_account
    return read_account(store)


def account_summaries(stores):
    from payments.store_accounts import account_summaries as read_accounts
    return read_accounts(stores)
