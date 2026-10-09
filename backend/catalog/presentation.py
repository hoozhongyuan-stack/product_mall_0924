from django.db.models import Prefetch

from payments.availability import enabled_payment_methods

from .description import present_description
from .models import Product, SkuGradePrice, SkuSpecSelection


def asset_data(asset):
    return {"assetId": str(asset.id), "kind": asset.kind, "contentType": asset.content_type,
            "byteSize": asset.byte_size, "width": asset.width, "height": asset.height,
            "originalName": asset.original_name,
            "adminUrl": f"/api/v1/admin/assets/{asset.id}/file"}


def public_asset_url(asset):
    return f"/api/v1/app/assets/{asset.id}/file" if asset else None


def category_data(item, stats=None):
    data = {"id": str(item.id), "parentId": str(item.parent_id) if item.parent_id else None,
            "name": item.name, "sortOrder": item.sort_order, "status": item.status,
            "revision": item.revision}
    if stats is not None:
        data.update({"productCount": stats.get(item.id, (0, 0))[0],
                     "onSaleProductCount": stats.get(item.id, (0, 0))[1]})
    return data


def grade_data(item):
    return {"id": str(item.id), "code": item.code, "name": item.name,
            "rank": item.rank, "enabled": item.enabled}


def unit_data(sku):
    unit = sku.current_unit
    return {"baseUnit": unit.base_unit, "saleUnit": unit.sale_unit, "ratio": unit.ratio} if unit else None


def sku_data(sku):
    selections = sku.selections.select_related("axis", "option").order_by("axis__sort_order")
    prices = SkuGradePrice.objects.filter(sku=sku, active=True).order_by("grade__rank")
    return {
        "skuId": str(sku.id), "rowKey": str(sku.id), "skuCode": sku.sku_code,
        "skuRevision": sku.revision, "productId": str(sku.product_id),
        "productRevision": sku.product.revision,
        "productNo": sku.product.product_no, "productName": sku.product.name,
        "productStatus": sku.product.status, "fulfillmentKind": sku.product.fulfillment_kind,
        "categoryId": str(sku.product.category_id),
        "specs": [{"name": item.axis.name, "value": item.option.value} for item in selections],
        "specOptionIds": [str(item.option_id) for item in selections],
        "listPriceFen": sku.list_price_fen,
        "gradePrices": [{"gradeId": str(item.grade_id), "priceFen": item.price_fen} for item in prices],
        "saleStatus": sku.sale_status, "unit": unit_data(sku),
    }


def product_data(product):
    axes = list(product.spec_axes.prefetch_related("options"))
    skus = product.skus.select_related("current_unit", "product").all()
    return {
        "productId": str(product.id), "productRevision": product.revision,
        "productNo": product.product_no, "name": product.name,
        "categoryId": str(product.category_id), "fulfillmentKind": product.fulfillment_kind,
        "redeemValidUntil": product.redeem_valid_until.isoformat() if product.redeem_valid_until else None,
        "status": product.status, "manuallyOffSale": product.manually_off_sale,
        "descriptionHtml": present_description(product.description_html),
        "mainImage": asset_data(product.main_image) if product.main_image_id else None,
        "galleryImages": [asset_data(row.asset) for row in product.gallery_images.select_related("asset").order_by("position")],
        "video": asset_data(product.video) if product.video_id else None,
        "unitConversion": product.unit_conversion,
        "specAxes": [{"id": str(axis.id), "name": axis.name, "sortOrder": axis.sort_order,
                      "options": [{"id": str(option.id), "value": option.value,
                                   "sortOrder": option.sort_order} for option in axis.options.all()]}
                     for axis in axes],
        "skus": [sku_data(sku) for sku in skus],
    }


def public_product_data(product, member=None):
    from inventory.availability import default_available_base_units
    from django.utils import timezone

    available_skus = product.skus.filter(sale_status="ON_SALE").select_related("current_unit").prefetch_related(
        Prefetch("selections", queryset=SkuSpecSelection.objects.select_related(
            "axis", "option").order_by("axis__sort_order", "axis_id"))
    ).order_by("created_at", "id")
    visible_skus = list(available_skus)
    warehouse, balances = default_available_base_units([sku.id for sku in visible_skus])
    grade_prices = {}
    if member and member.grade.enabled:
        grade_prices = {row.sku_id: row.price_fen for row in SkuGradePrice.objects.filter(
            sku_id__in=[sku.id for sku in visible_skus], grade_id=member.grade_id, active=True)}
    skus = []
    for sku in visible_skus:
        selections = sku.selections.all()
        available = balances.get(sku.id, 0) // sku.current_unit.ratio if warehouse and sku.current_unit else 0
        skus.append({"skuId": str(sku.id), "skuCode": sku.sku_code,
                     "specs": [{"name": item.axis.name, "value": item.option.value} for item in selections],
                     "listPriceFen": sku.list_price_fen,
                     "applicablePriceFen": grade_prices.get(sku.id, sku.list_price_fen),
                     "priceSource": "GRADE" if sku.id in grade_prices else "DAILY",
                     "availableQuantity": available, "cartEligible": available > 0,
                     "unit": unit_data(sku)})
    validity_ready = (product.fulfillment_kind != Product.Fulfillment.REDEEM or
                      bool(product.redeem_valid_until and product.redeem_valid_until >= timezone.localdate()))
    skus = [{**row, "cartEligible": row["cartEligible"] and validity_ready} for row in skus]
    has_stock = any(row["cartEligible"] for row in skus)
    return {"productId": str(product.id), "productNo": product.product_no,
            "name": product.name, "categoryId": str(product.category_id),
            "fulfillmentKind": product.fulfillment_kind, "descriptionHtml": present_description(product.description_html, public=True),
            "redeemValidUntil": product.redeem_valid_until.isoformat() if product.redeem_valid_until else None,
            "mainImageUrl": public_asset_url(product.main_image),
            "galleryImageUrls": [public_asset_url(row.asset) for row in
                                 product.gallery_images.select_related("asset").order_by("position")],
            "videoUrl": public_asset_url(product.video),
            "skus": skus, "purchasable": has_stock and bool(enabled_payment_methods()),
            "cartEligible": has_stock,
            "availabilityCode": "AVAILABLE" if has_stock else "REDEEM_VALIDITY_UNAVAILABLE" if not validity_ready else "STOCK_NOT_READY",
            "availabilityMessage": ("可选购，结算前将重新核对价格和库存。" if has_stock else
                                    "核销有效期未配置或已结束，暂不可购买。" if not validity_ready else
                                    "库存尚未配置，暂不可购买。" if not warehouse else "暂无可售库存。")}
