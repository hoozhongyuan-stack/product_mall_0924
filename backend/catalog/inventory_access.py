"""Catalog's read contract for inventory, without pricing or edit access."""
from django.db.models import Prefetch, Q
from .models import Sku, SkuSpecSelection


def stock_skus(keyword=""):
    rows = Sku.objects.filter(current_unit__isnull=False).select_related("product", "current_unit")
    if keyword:
        rows = rows.filter(Q(sku_code__icontains=keyword) | Q(product__name__icontains=keyword)
                           | Q(product__product_no__icontains=keyword)
                           | Q(selections__option__value__icontains=keyword)).distinct()
    return rows


def selection_skus(keyword=""):
    return stock_skus(keyword).select_related("product__main_image").prefetch_related(
        Prefetch("selections", queryset=SkuSpecSelection.objects.select_related("axis", "option")
                 .order_by("axis__sort_order", "axis_id"), to_attr="inventory_specs"))


def stock_sku_data(row):
    image = row.product.main_image
    return {"skuId": str(row.id), "skuCode": row.sku_code,
            "productName": row.product.name, "productNo": row.product.product_no,
            "specs": [{"name": item.axis.name, "value": item.option.value} for item in row.inventory_specs],
            "mainImage": {"assetId": str(image.id),
                          "adminUrl": f"/api/v1/admin/assets/{image.id}/file"} if image else None,
            "baseUnit": row.current_unit.base_unit, "saleUnit": row.current_unit.sale_unit,
            "ratio": row.current_unit.ratio, "unitVersionId": str(row.current_unit_id)}


def lock_stock_skus(ids):
    return (Sku.objects.select_for_update(of=("self",)).select_related("current_unit", "product")
            .filter(id__in=ids).order_by("id"))
