"""Inventory-owned read contract for catalog SKU identity changes."""

from .models import InboundLine, OutboundLine, StocktakeLine, InventoryBalance, InventoryLedger


def referenced_sku_ids(sku_ids):
    """Return SKUs whose draft or committed inventory records depend on identity.

    A balance may outlive an inbound document in later slices, so check each
    owned reference independently. Callers lock SKU rows before write checks.
    """
    identifiers = set(sku_ids)
    if not identifiers:
        return set()
    sources = (InboundLine, OutboundLine, StocktakeLine, InventoryBalance, InventoryLedger)
    return set().union(*(
        set(source.objects.filter(sku_id__in=identifiers).values_list("sku_id", flat=True))
        for source in sources
    ))
