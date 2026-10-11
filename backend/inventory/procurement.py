"""Authorized member receipts increment physical pools without releasing reserves."""
from django.db import transaction
from catalog.models import Sku
from stores.access import require_staff, StoreError
from .models import Warehouse, InventoryBalance, InventoryLedger
from .pool_access import resolve_anchor_ids


@transaction.atomic
def receive_procurement(order, member):
    require_staff(member,order.store_id,'receiving')
    Warehouse.objects.select_for_update().get(pk=order.store.warehouse_id)
    lines = list(order.lines.select_related('unit_version').order_by('sku_id'))
    skus = {x.pk:x for x in Sku.objects.select_for_update(of=('self',)).select_related('current_unit').filter(pk__in=[x.sku_id for x in lines]).order_by('pk')}
    anchors = resolve_anchor_ids(skus)
    for line in lines:
        if skus[line.sku_id].current_unit_id!=line.unit_version_id and skus[line.sku_id].current_unit_id!=line.reviewed_current_unit_id:
            raise StoreError('单位版本已变化，须平台重新核对。','UNIT_REVISION_CONFLICT',409)
        if not skus[line.sku_id].current_unit or anchors.get(line.sku_id)!=line.anchor_sku_id or skus[line.sku_id].current_unit.base_unit!=line.base_unit:
            raise StoreError('库存池关系或基础单位已变化，须平台处理本单，不能自动入库。','PROCUREMENT_POOL_CONFLICT',409)
    for anchor_id in sorted(set(anchors.values()),key=str):
        Sku.objects.select_for_update().get(pk=anchor_id)
        balance,_=InventoryBalance.objects.select_for_update().get_or_create(warehouse_id=order.store.warehouse_id,sku_id=anchor_id)
        for line in [x for x in lines if x.anchor_sku_id==anchor_id]:
            amount=line.quantity*line.ratio
            before=balance.on_hand_base_units
            if before+amount>1000000000000:
                raise StoreError('入库后库存超出允许范围。')
            InventoryLedger.objects.create(warehouse_id=order.store.warehouse_id,sku_id=anchor_id,unit_version=line.unit_version,
                procurement_line=line,store_member=member,movement_type='PROCURE',operation_unit=line.sale_unit,operation_quantity=line.quantity,
                ratio=line.ratio,delta_base_units=amount,balance_before=before,balance_after=before+amount,reason='前置仓进货整单验收入库')
            balance.on_hand_base_units += amount
        balance.save(update_fields=['on_hand_base_units','updated_at'])
