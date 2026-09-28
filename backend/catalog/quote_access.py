"""Catalog-owned, read-only SKU snapshot used by checkout quotes."""

from .models import Category, Product, Sku, SkuGradePrice, SkuSpecSelection


def quote_catalog_rows(sku_ids, member=None):
    skus = list(Sku.objects.filter(id__in=sku_ids).select_related(
        "product", "product__category", "product__category__parent", "current_unit"))
    specs = {sku.id: [] for sku in skus}
    for selection in SkuSpecSelection.objects.filter(sku_id__in=sku_ids).select_related(
            "axis", "option").order_by("sku_id", "axis__sort_order", "axis_id"):
        specs[selection.sku_id].append({"name": selection.axis.name, "value": selection.option.value})
    grade_prices = {}
    if member and member.grade.enabled:
        grade_prices = {price.sku_id: price.price_fen for price in SkuGradePrice.objects.filter(
            sku_id__in=sku_ids, grade_id=member.grade_id, active=True)}
    rows = {}
    for sku in skus:
        product = sku.product
        category = product.category
        unit = sku.current_unit
        visible = (product.status == Product.Status.ON_SALE and sku.sale_status == Sku.SaleStatus.ON_SALE
                   and product.main_image_id is not None and category.status == Category.Status.ACTIVE
                   and category.parent is not None and category.parent.status == Category.Status.ACTIVE)
        rows[sku.id] = {
            "productId": str(product.id) if visible else None,
            "name": product.name if visible else "商品已失效",
            "imageUrl": f"/api/v1/app/assets/{product.main_image_id}/file" if visible else None,
            "fulfillmentKind": product.fulfillment_kind if visible else None,
            "redeemValidUntil": product.redeem_valid_until.isoformat() if visible and product.redeem_valid_until else None,
            "onSale": visible,
            "saleUnit": unit.sale_unit if visible and unit else None,
            "ratio": unit.ratio if visible and unit else None,
            "specs": specs[sku.id] if visible else [],
            "priceFen": grade_prices.get(sku.id, sku.list_price_fen) if visible else 0,
            "priceSource": ("GRADE" if sku.id in grade_prices else "DAILY") if visible else None,
        }
    return rows
