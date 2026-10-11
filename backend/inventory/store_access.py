"""Store stock numbers operate on the existing warehouse/pool balances."""
from django.db import transaction
from catalog.models import Sku
from stores.access import StoreError
from .models import InventoryBalance, InventoryLedger, Warehouse
from .pool_access import resolve_anchor_ids, ensure_independent_pool


def available_base_units(store,sku_ids):
    anchors = resolve_anchor_ids(sku_ids)
    rows = InventoryBalance.objects.filter(warehouse_id=store.warehouse_id,sku_id__in=set(anchors.values()))
    values = {row.sku_id: row.on_hand_base_units-row.reserved_base_units for row in rows}
    return {sku_id:values.get(anchor,0) for sku_id,anchor in anchors.items()}


def shortage_product_ids(store):
    """SQL read projection: any active sale SKU cannot supply one sale unit."""
    from django.db.models import F, OuterRef, Subquery, Value, BigIntegerField
    from django.db.models.functions import Coalesce
    from .models import StockPoolSku
    anchor = StockPoolSku.objects.filter(sku_id=OuterRef('pk')).values('pool__anchor_sku_id')[:1]
    skus = Sku.objects.filter(sale_status='ON_SALE', current_unit__isnull=False).annotate(
        stock_anchor=Coalesce(Subquery(anchor), F('id')))
    balance = InventoryBalance.objects.filter(warehouse_id=store.warehouse_id,
        sku_id=OuterRef('stock_anchor')).annotate(
            available=F('on_hand_base_units')-F('reserved_base_units')).values('available')[:1]
    return skus.annotate(available=Coalesce(Subquery(balance), Value(0),
        output_field=BigIntegerField())).filter(available__lt=F('current_unit__ratio')).values('product_id')


def set_available_stock(store,sku_id,available,expected,member):
    if type(available) is not int or not 0 <= available <= 1000000000 or type(expected) is not int or expected < 0:
        raise StoreError('库存须为非负整数，且必须携带读取时的库存值。')
    with transaction.atomic():
        # Warehouse lock serializes a first stock write where no balance exists yet.
        Warehouse.objects.select_for_update().get(pk=store.warehouse_id)
        sku = Sku.objects.select_for_update(of=('self',)).select_related('current_unit','product').filter(pk=sku_id).first()
        if not sku or sku.product.fulfillment_kind != 'SHIP' or not sku.current_unit:
            raise StoreError('该商品不支持实物库存设置。')
        pool = ensure_independent_pool(sku)
        anchor = Sku.objects.select_related('current_unit').get(pk=pool.anchor_sku_id)
        balance,_ = InventoryBalance.objects.select_for_update().get_or_create(warehouse_id=store.warehouse_id,sku_id=anchor.id)
        current = balance.on_hand_base_units-balance.reserved_base_units
        if current != expected:
            raise StoreError('库存已变化，请刷新后重新填写。','STOCK_CHANGED',409)
        after = available+balance.reserved_base_units
        delta = after-balance.on_hand_base_units
        if delta:
            import uuid
            InventoryLedger.objects.create(warehouse_id=store.warehouse_id,sku=anchor,unit_version=anchor.current_unit,
                movement_type='STORE_SET',store_adjustment_id=uuid.uuid4(),store_member=member,
                operation_unit=pool.base_unit,operation_quantity=abs(delta),ratio=1,delta_base_units=delta,
                balance_before=balance.on_hand_base_units,balance_after=after,reason='前置仓可售库存填写')
            balance.on_hand_base_units = after
            balance.save(update_fields=['on_hand_base_units','updated_at'])
        return available
