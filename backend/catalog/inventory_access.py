"""Catalog's read contract for inventory operations.

Inventory may identify a SKU and read its current base/sale unit version. It
does not receive prices or edit catalog state through this module.
"""

from django.db.models import Q

from .models import Sku


def stock_skus(keyword=""):
    rows = Sku.objects.filter(current_unit__isnull=False).select_related("product", "current_unit")
    if keyword:
        rows = rows.filter(Q(sku_code__icontains=keyword) | Q(product__name__icontains=keyword))
    return rows


def lock_stock_skus(ids):
    return (Sku.objects.select_for_update(of=("self",)).select_related("current_unit", "product")
            .filter(id__in=ids).order_by("id"))
