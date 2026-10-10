"""Read-only default-warehouse availability for quoting and storefront views."""

from .models import InventoryBalance, StockPoolSku, Warehouse
from .pool_access import resolve_anchor_ids
from django.db.models import Exists, F, OuterRef, Subquery
from django.db.models.functions import Coalesce
from catalog.models import Sku


def default_available_base_units(sku_ids):
    warehouse = Warehouse.objects.filter(is_default=True, enabled=True).first()
    if warehouse is None:
        return None, {}
    anchors = resolve_anchor_ids(sku_ids)
    rows = InventoryBalance.objects.filter(warehouse=warehouse, sku_id__in=set(anchors.values()))
    available = {row.sku_id: max(0, row.on_hand_base_units - row.reserved_base_units) for row in rows}
    return warehouse, {sku_id: available.get(anchor, 0) for sku_id, anchor in anchors.items()}


def product_has_available_stock(warehouse_id=None):
    """Expression for a Product queryset; keeps the list to one data query."""
    anchor = StockPoolSku.objects.filter(sku_id=OuterRef("pk")).values("pool__anchor_sku_id")[:1]
    stocked_sku = Sku.objects.filter(
        product_id=OuterRef("pk"), sale_status=Sku.SaleStatus.ON_SALE,
        current_unit__isnull=False,
    ).annotate(stock_anchor=Coalesce(Subquery(anchor), F("id")))
    warehouse_filter = {'warehouse_id': warehouse_id} if warehouse_id else {'warehouse__is_default': True}
    balance = InventoryBalance.objects.filter(
        sku_id=OuterRef("stock_anchor"), **warehouse_filter,
        warehouse__enabled=True,
        on_hand_base_units__gte=F("reserved_base_units") + OuterRef("current_unit__ratio"),
    )
    stocked_sku = stocked_sku.annotate(has_stock=Exists(balance)).filter(has_stock=True)
    return Exists(stocked_sku)
