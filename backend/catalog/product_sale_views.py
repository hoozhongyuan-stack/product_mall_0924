"""Admin SPU sale gate endpoints and batch impact preview."""

from common.http import method, parse_json_object
from accounts.security import error, require, response

from .models import Product, Sku
from .sale_state import change_product_sale_status, validate_product_publish
from .validation import CatalogError, list_field, number_field, object_field, uuid_field


def _body(request):
    return parse_json_object(request, max_bytes=262144, error_type=CatalogError)


def _failure(request, exc):
    return error(request, exc.status, exc.code, str(exc))


def _sale_actor(request):
    actor, bad = require(request, "catalog.write")
    if bad:
        return actor, bad
    _, bad = require(request, "sku.status.write")
    return actor, bad


def product_sale_status_view(request, product_id):
    bad = method(request, "PATCH")
    if bad:
        return bad
    actor, bad = _sale_actor(request)
    if bad:
        return bad
    try:
        values = _body(request)
        if set(values) - {"expectedRevision", "saleStatus", "initialSkuIds"}:
            raise CatalogError("商品上下架请求含有不支持的字段。")
        status = values.get("saleStatus")
        ids = _initial_sku_ids(values.get("initialSkuIds", []))
        product = change_product_sale_status(request, actor, product_id,
            number_field(values.get("expectedRevision"), "预期修订号", 1), status, ids)
        return response(request, {"productId": str(product.id), "status": product.status,
                                  "productRevision": product.revision})
    except CatalogError as exc:
        return _failure(request, exc)


def _initial_sku_ids(raw):
    ids = [uuid_field(item, "首次上架 SKU ID") for item in list_field(raw, "首次上架 SKU", 100)]
    if len(ids) != len(set(ids)):
        raise CatalogError("首次上架 SKU 不能重复。")
    return ids


def _product_sale_items(values):
    if set(values) != {"saleStatus", "items"}:
        raise CatalogError("商品上下架须提供目标状态和商品。")
    status = values["saleStatus"]
    if status not in (Product.Status.ON_SALE, Product.Status.OFF_SALE):
        raise CatalogError("商品销售状态不正确。")
    items = []
    seen = set()
    for raw in list_field(values["items"], "商品", 100, 1):
        row = object_field(raw, "商品")
        if set(row) - {"productId", "expectedRevision", "initialSkuIds"} or not {"productId", "expectedRevision"} <= set(row):
            raise CatalogError("商品须提供 ID、预期修订号及可选的首次上架 SKU。")
        identifier = uuid_field(row["productId"], "商品 ID")
        if identifier in seen:
            raise CatalogError("商品不能重复。")
        seen.add(identifier)
        items.append((identifier, number_field(row["expectedRevision"], "预期修订号", 1),
                      _initial_sku_ids(row.get("initialSkuIds", []))))
    return status, items


def batch_product_sale_preview_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = _sale_actor(request)
    if bad:
        return bad
    try:
        status, items = _product_sale_items(_body(request))
        products = {item.id: item for item in Product.objects.select_related("category__parent")
                    .filter(id__in=[identifier for identifier, _, _ in items])}
        sku_groups = {}
        for sku in Sku.objects.select_related("current_unit").filter(
                product_id__in=products).order_by("sku_code", "id"):
            sku_groups.setdefault(sku.product_id, []).append(sku)
        rows = []
        for identifier, revision, selected_ids in items:
            product = products.get(identifier)
            skus = sku_groups.get(identifier, [])
            reason = None
            try:
                if product is None:
                    raise CatalogError("商品不存在。", "NOT_FOUND", 404)
                if product.revision != revision:
                    raise CatalogError("商品已被其他人修改。", "REVISION_CONFLICT", 409)
                if status == Product.Status.OFF_SALE:
                    if selected_ids:
                        raise CatalogError("下架商品时不能提供首次上架 SKU。")
                    if product.status == Product.Status.DRAFT:
                        raise CatalogError("草稿商品尚未上架。")
                    if product.manually_off_sale:
                        raise CatalogError("商品已手动下架。")
                elif product.status == Product.Status.DRAFT:
                    if len(selected_ids) != len([sku for sku in skus if sku.id in selected_ids]):
                        raise CatalogError("所选 SKU 不属于此商品。")
                    validate_product_publish(product, [sku for sku in skus if sku.id in selected_ids])
                else:
                    if selected_ids:
                        raise CatalogError("非草稿商品不能重新选择首次上架 SKU。")
                    if product.status == Product.Status.ON_SALE and not product.manually_off_sale:
                        raise CatalogError("商品已在售。")
                    validate_product_publish(product, [sku for sku in skus if sku.sale_status == Sku.SaleStatus.ON_SALE])
            except CatalogError as exc:
                reason = str(exc)
            rows.append({"productId": str(identifier), "productNo": product.product_no if product else "",
                         "productRevision": revision, "status": product.status if product else None,
                         "skuCount": len(skus), "onSaleSkuCount": sum(sku.sale_status == Sku.SaleStatus.ON_SALE for sku in skus),
                         "skuOptions": [{"skuId": str(sku.id), "skuCode": sku.sku_code,
                                         "hasUnit": bool(sku.current_unit_id)} for sku in skus] if product and product.status == Product.Status.DRAFT else [],
                         "canChange": reason is None, "reason": reason})
        return response(request, {"items": rows})
    except CatalogError as exc:
        return _failure(request, exc)


def batch_product_sale_status_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = _sale_actor(request)
    if bad:
        return bad
    try:
        status, items = _product_sale_items(_body(request))
        results = []
        for identifier, revision, initial_ids in items:
            try:
                product = change_product_sale_status(request, actor, identifier, revision, status, initial_ids)
                results.append({"id": str(identifier), "success": True, "revision": product.revision,
                                "status": product.status})
            except CatalogError as exc:
                results.append({"id": str(identifier), "success": False, "code": exc.code,
                                "message": str(exc)})
        count = sum(row["success"] for row in results)
        return response(request, {"results": results, "successCount": count,
                                  "failedCount": len(results) - count})
    except CatalogError as exc:
        return _failure(request, exc)
