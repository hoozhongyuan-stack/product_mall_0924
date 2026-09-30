"""Paginated SKU selection; inventory figures are a read-time reference only."""
from catalog.inventory_access import selection_skus, stock_sku_data
from .models import InventoryBalance, Warehouse
from .pool_access import pool_info_for_skus, resolve_anchor_ids
from .validation import InventoryError, uuid_field


def selection_page(keyword, page, size, warehouse_id=None):
    warehouse = None
    if warehouse_id:
        identifier = uuid_field(warehouse_id, "仓库 ID")
        warehouse = Warehouse.objects.filter(pk=identifier).first()
        if warehouse is None:
            raise InventoryError("仓库不存在。", "NOT_FOUND", 404)
    rows = selection_skus(keyword)
    total = rows.count()
    selected = list(rows.order_by("sku_code", "id")[(page - 1) * size:page * size])
    anchors = resolve_anchor_ids([row.id for row in selected])
    pool_info = pool_info_for_skus([row.id for row in selected])
    balances = {row.sku_id: row for row in InventoryBalance.objects.filter(
        warehouse=warehouse, sku_id__in=set(anchors.values()))} if warehouse else {}
    items = []
    for row in selected:
        balance = balances.get(anchors[row.id])
        on_hand = balance.on_hand_base_units if balance else 0
        reserved = balance.reserved_base_units if balance else 0
        stock = {"warehouseId": str(warehouse.id), "onHandBaseUnits": on_hand,
                 "reservedBaseUnits": reserved, "availableBaseUnits": on_hand - reserved} if warehouse else None
        items.append({**stock_sku_data(row), **pool_info[row.id], "warehouseStock": stock})
    return {"items": items, "page": page, "pageSize": size, "total": total}
