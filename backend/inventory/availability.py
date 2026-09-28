"""Read-only default-warehouse availability for quoting and storefront views."""

from .models import InventoryBalance, Warehouse
from django.db.models import Exists, F, OuterRef
from catalog.models import Sku


def default_available_base_units(sku_ids):
    warehouse = Warehouse.objects.filter(is_default=True, enabled=True).first()
    if warehouse is None:
        return None, {}
    rows = InventoryBalance.objects.filter(warehouse=warehouse, sku_id__in=sku_ids)
    return warehouse, {row.sku_id: max(0, row.on_hand_base_units - row.reserved_base_units) for row in rows}


def product_has_available_stock():
    """Expression for a Product queryset; keeps the list to one data query."""
    stocked_sku = Sku.objects.filter(
        product_id=OuterRef("pk"), sale_status=Sku.SaleStatus.ON_SALE,
        current_unit__isnull=False, inventory_balances__warehouse__is_default=True,
        inventory_balances__warehouse__enabled=True,
        inventory_balances__on_hand_base_units__gte=(
            F("inventory_balances__reserved_base_units") + F("current_unit__ratio")),
    )
    return Exists(stocked_sku)
