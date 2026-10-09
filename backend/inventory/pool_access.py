"""Inventory-owned mapping from a sale SKU to one physical stock identity."""

from django.db import transaction

from catalog.models import Product, Sku
from orders.models import OrderLine

from .models import (InboundLine, InventoryBalance, InventoryLedger,
                     InventoryReservation, OutboundLine, StockPool, StockPoolSku,
                     StocktakeLine)


class PoolBindingError(ValueError):
    pass


def resolve_anchor_ids(sku_ids):
    """Return {sale_sku_id: balance_anchor_sku_id}; unbound SKUs are independent."""
    ids = list(dict.fromkeys(sku_ids))
    mapped = dict(StockPoolSku.objects.filter(sku_id__in=ids).values_list(
        "sku_id", "pool__anchor_sku_id"))
    return {sku_id: mapped.get(sku_id, sku_id) for sku_id in ids}


def resolve_anchor_id(sku_id):
    return resolve_anchor_ids([sku_id])[sku_id]


def pool_base_unit(sku_id):
    """Base unit fixed by an existing pool, or None before first stock operation."""
    anchor = resolve_anchor_id(sku_id)
    return StockPool.objects.filter(anchor_sku_id=anchor).values_list("base_unit", flat=True).first()


def sku_has_stock_history(sku_id):
    return (InventoryBalance.objects.filter(sku_id=sku_id).exists() or
            InventoryReservation.objects.filter(balance__sku_id=sku_id).exists() or
            InventoryLedger.objects.filter(sku_id=sku_id).exists() or
            OrderLine.objects.filter(sku_id=sku_id).exists() or
            InboundLine.objects.filter(sku_id=sku_id).exists() or
            OutboundLine.objects.filter(sku_id=sku_id).exists() or
            StocktakeLine.objects.filter(sku_id=sku_id).exists())


def pool_change_allowed(sku_id, *, new_base_unit=None, removing=False):
    """A history-free independent pool may be edited; shared physical identity may not."""
    binding = StockPoolSku.objects.select_related("pool").filter(sku_id=sku_id).first()
    if binding is None:
        return True
    if not removing and binding.pool.base_unit == new_base_unit:
        return True
    if sku_has_stock_history(sku_id):
        return False
    if removing and binding.pool.anchor_sku_id != sku_id:
        return True
    return (binding.pool.anchor_sku_id == sku_id and
            not StockPoolSku.objects.filter(pool_id=binding.pool_id).exclude(sku_id=sku_id).exists())


def update_empty_pool_base_unit(sku_id, new_base_unit):
    """Keep a never-stocked independent pool aligned with a draft SKU unit edit."""
    binding = StockPoolSku.objects.select_for_update().select_related("pool").filter(sku_id=sku_id).first()
    if binding is None or binding.pool.base_unit == new_base_unit:
        return
    if not pool_change_allowed(sku_id, new_base_unit=new_base_unit):
        raise PoolBindingError("共享或已有历史的库存池不能修改基础单位。")
    binding.pool.base_unit = new_base_unit
    binding.pool.save(update_fields=["base_unit"])


def remove_unused_pool_membership(sku_id):
    """Release pool ownership before deleting a history-free draft SKU."""
    binding = StockPoolSku.objects.select_for_update().select_related("pool").filter(sku_id=sku_id).first()
    if binding is None:
        return
    if not pool_change_allowed(sku_id, removing=True):
        raise PoolBindingError("共享或已有历史的库存池不能删除 SKU。")
    pool = binding.pool
    binding.delete()
    if pool.anchor_sku_id == sku_id:
        pool.delete()


def pool_info_for_skus(sku_ids):
    """Read projection for admin views; all members of a pool share one balance."""
    ids = list(dict.fromkeys(sku_ids))
    anchors = resolve_anchor_ids(ids)
    pools = {pool.anchor_sku_id: pool for pool in StockPool.objects.filter(
        anchor_sku_id__in=set(anchors.values()))}
    shared_anchors = set(StockPoolSku.objects.filter(
        pool__anchor_sku_id__in=set(anchors.values())).exclude(
            sku_id__in=set(anchors.values())).values_list("pool__anchor_sku_id", flat=True))
    return {sku_id: {"poolId": str(pools[anchor].id) if anchor in pools else None,
                     "anchorSkuId": str(anchor),
                     "poolBaseUnit": pools[anchor].base_unit if anchor in pools else None,
                     "shared": anchor in shared_anchors}
            for sku_id, anchor in anchors.items()}


def ensure_independent_pool(sku):
    """Create the independent pool on first stock operation, retaining legacy balance keys."""
    if sku.current_unit_id is None:
        raise PoolBindingError("SKU 未配置库存单位。")
    existing = StockPoolSku.objects.filter(sku=sku).select_related("pool").first()
    if existing:
        if existing.pool.base_unit != sku.current_unit.base_unit:
            raise PoolBindingError("SKU 基础单位与库存池不一致。")
        return existing.pool
    pool, _ = StockPool.objects.get_or_create(
        anchor_sku=sku, defaults={"base_unit": sku.current_unit.base_unit})
    if pool.base_unit != sku.current_unit.base_unit:
        raise PoolBindingError("SKU 基础单位与库存池不一致。")
    StockPoolSku.objects.get_or_create(sku=sku, defaults={"pool": pool})
    return StockPoolSku.objects.select_related("pool").get(sku=sku).pool


def bind_sku_to_pool(sku_id, pool_id, expected_sku_revision):
    """Only a never-stocked SKU may join a same-product, same-unit pool.

    This deliberately refuses stock merges and historical reinterpretation.
    """
    with transaction.atomic():
        product_id = Sku.objects.values_list("product_id", flat=True).get(pk=sku_id)
        Product.objects.select_for_update().get(pk=product_id)
        sku = Sku.objects.select_for_update(of=("self",)).select_related("current_unit", "product").get(pk=sku_id)
        if sku.product.unit_conversion is not None:
            raise PoolBindingError("多单位商品的库存关系由规格自动确定，不能手动绑定。")
        if sku.revision != expected_sku_revision:
            raise PoolBindingError("SKU 已被修改，请刷新后重试。")
        pool = StockPool.objects.select_for_update().select_related("anchor_sku").get(pk=pool_id)
        if (sku.product_id != pool.anchor_sku.product_id or not sku.current_unit_id or
                sku.current_unit.base_unit != pool.base_unit):
            raise PoolBindingError("共享库存池要求同一商品及相同基础单位。")
        current = StockPoolSku.objects.select_for_update().filter(sku=sku).first()
        if current and current.pool_id == pool.id:
            return pool, False
        if sku_has_stock_history(sku.id):
            raise PoolBindingError("SKU 已有库存或历史单据，须先人工对账，不能直接并池。")
        if current:
            if current.pool.anchor_sku_id != sku.id:
                raise PoolBindingError("SKU 已绑定其他共享库存池。")
            if StockPoolSku.objects.filter(pool=current.pool).exclude(sku=sku).exists():
                raise PoolBindingError("SKU 的原库存池已有其他成员。")
            old_pool = current.pool
            current.pool = pool
            current.save(update_fields=["pool"])
            old_pool.delete()
        else:
            StockPoolSku.objects.create(sku=sku, pool=pool)
        return pool, True


def stock_history_ids(sku_ids):
    """Batch history projection, including zero balances and draft documents."""
    identifiers = set(sku_ids)
    if not identifiers:
        return set()
    sources = (InventoryBalance, InventoryLedger, OrderLine, InboundLine, OutboundLine, StocktakeLine)
    result = set().union(*(set(model.objects.filter(sku_id__in=identifiers)
                              .values_list('sku_id', flat=True)) for model in sources))
    return result | set(InventoryReservation.objects.filter(balance__sku_id__in=identifiers)
                        .values_list('balance__sku_id', flat=True))


def validate_unit_groups(groups):
    """Reject changing any physical identity after its first inventory/order reference."""
    ids = {row['matched'].id for group in groups for row in group if row['matched']}
    bindings = dict(StockPoolSku.objects.filter(sku_id__in=ids).values_list('sku_id', 'pool_id'))
    members = {}
    for pool_id, sku_id in StockPoolSku.objects.filter(pool_id__in=set(bindings.values())).values_list('pool_id', 'sku_id'):
        members.setdefault(pool_id, set()).add(sku_id)
    history = stock_history_ids(ids | set().union(*members.values()) if members else ids)
    for group in groups:
        matched = [row['matched'] for row in group if row['matched']]
        for sku in matched:
            previous = members.get(bindings.get(sku.id), {sku.id})
            if not previous & history:
                continue
            if {item.id for item in matched} != previous or len(matched) != len(group):
                raise PoolBindingError('已有库存或单据的单位规格组合不能修改库存关系。')
            for row in group:
                current = row['matched'].current_unit
                if not current or (current.base_unit, current.sale_unit, current.ratio) != (
                        row['unit']['base_unit'], row['unit']['sale_unit'], row['unit']['ratio']):
                    raise PoolBindingError('已有库存或单据的单位换算关系不能修改。')


def release_empty_product_groups(product_id):
    """Release only history-free groups before a complete draft specification rewrite."""
    pools = list(StockPool.objects.select_for_update().filter(anchor_sku__product_id=product_id))
    members = {}
    for pool_id, sku_id in StockPoolSku.objects.filter(pool_id__in=[pool.id for pool in pools]).values_list('pool_id', 'sku_id'):
        members.setdefault(pool_id, set()).add(sku_id)
    history = stock_history_ids(set().union(*members.values()) if members else set())
    empty_ids = [pool.id for pool in pools if not members.get(pool.id, set()) & history]
    StockPoolSku.objects.filter(pool_id__in=empty_ids).delete()
    StockPool.objects.filter(id__in=empty_ids).delete()


def configure_unit_groups(config, groups):
    """Assign one base-unit stock identity per non-unit specification combination."""
    existing_ids = set(StockPoolSku.objects.filter(sku_id__in=[row['sku'].id for group in groups for row in group])
                       .values_list('sku_id', flat=True))
    for group in groups:
        if any(row['sku'].id in existing_ids for row in group):
            # A history-bearing group was validated and retained intact.
            continue
        base_id = config['baseOptionKey'] if config else None
        anchor = next((row['sku'] for row in group if base_id in [str(option) for _, option in row['selection']]), group[0]['sku'])
        pool = StockPool.objects.create(anchor_sku=anchor, base_unit=anchor.current_unit.base_unit)
        StockPoolSku.objects.bulk_create([StockPoolSku(sku=row['sku'], pool=pool) for row in group])
