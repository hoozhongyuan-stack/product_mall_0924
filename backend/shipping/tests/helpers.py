from uuid import uuid4
from shipping.models import ShippingPolicy


def configure_zone(store,fee=600,radius=3000,**overrides):
    policy=ShippingPolicy.objects.get(pk=1)
    row=dict(id=str(uuid4()),storeId=str(store.pk),name='测试配送区',kind='CIRCLE',center=dict(latitude=float(store.latitude),longitude=float(store.longitude)),radiusMeters=radius,polygon=[],priority=10,feeMode='FIXED',feeFen=fee,firstMeters=3000,extraMeters=1000,extraFeeFen=200,minimumFen=0,freeThresholdFen=None,startTime='00:00',endTime='23:59',enabled=True)
    row.update(overrides)
    policy.zones=[item for item in policy.zones if item['storeId']!=str(store.pk)]+[row]
    policy.save(update_fields=['zones'])
    return row


def physical_selection(body):
    """Explicit test factory: physical selections carry the chosen front warehouse."""
    if body.get('storeId') or body.get('diningSessionId'):return body
    from catalog.models import Sku
    from inventory.models import Warehouse
    from stores.models import Store,StoreProduct
    products={row.product_id for row in Sku.objects.filter(pk__in=[item['skuId'] for item in body['items']],product__fulfillment_kind='SHIP')}
    if not products:return body
    warehouse=Warehouse.objects.get(is_default=True)
    store,_=Store.objects.get_or_create(warehouse=warehouse,defaults=dict(name='报价测试前置仓',supported_modes=['EXPRESS']))
    for product_id in products:StoreProduct.objects.update_or_create(store=store,product_id=product_id,defaults={'on_sale':True})
    return {**body,'storeId':str(store.pk),'deliveryMode':'EXPRESS'}


def selected_physical_quote(body,member,*args,**kwargs):
    """Test only factory, delegates the explicit selection to real quote service."""
    from checkout.service import create_quote
    return create_quote(physical_selection(body),member,*args,**kwargs)


def configure_courier_fee(fee):
    policy,_=ShippingPolicy.objects.get_or_create(pk=1)
    policy.templates=[{**row,'firstFeeFen':fee} if not row['productIds'] and row['mode']=='FIXED' else row for row in policy.templates]
    policy.save(update_fields=['templates'])
