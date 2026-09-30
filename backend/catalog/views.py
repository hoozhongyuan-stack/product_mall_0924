import hashlib
import json
from itertools import groupby

from django.core import signing
from django.db import transaction
from django.db.models import Count, Exists, Max, Min, OuterRef, Q, Subquery
from django.utils import timezone

from common.http import offset_response, parse_json_object, method
from accounts.security import audit, error, permissions, require, response

from .description import parse_description, set_description_images
from .media import resolve_media, set_gallery
from .models import BatchCategoryRequest, Category, MemberGrade, Product, Sku, SkuGradePrice, SkuSpecSelection, SkuUnitVersion
from .presentation import asset_data, category_data, grade_data, product_data, public_product_data, sku_data
from .sale_state import change_sku_sale_status
from .service import active_leaf, create_product, parse_prices, parse_unit
from .spec_edit import preview_specs, save_specs
from .validation import CatalogError, list_field, number_field, object_field, redeem_valid_until, text_field, uuid_field
from inventory.sku_guards import referenced_sku_ids
from inventory.pool_access import PoolBindingError, update_empty_pool_base_unit
from payments.availability import enabled_payment_methods


def failure(request, exc):
    return error(request, exc.status, exc.code, str(exc))


def body(request):
    return parse_json_object(request, max_bytes=262144, error_type=CatalogError)


def page_args(request):
    try:
        page = int(request.GET.get("page", "1"))
        size = int(request.GET.get("pageSize", "20"))
    except ValueError as exc:
        raise CatalogError("分页参数不正确。") from exc
    if not 1 <= page <= 100000 or not 1 <= size <= 100:
        raise CatalogError("分页参数不正确。")
    return page, size


def category_parent(raw):
    if raw is None:
        return None
    parent = Category.objects.select_for_update().filter(id=uuid_field(raw, "父分类 ID")).first()
    if not parent or parent.parent_id or parent.status != Category.Status.ACTIVE:
        raise CatalogError("父分类必须是已启用的一级分类。")
    return parent


def category_totals(categories):
    parent_ids = {item.id: item.parent_id for item in categories}
    totals = {item.id: [0, 0] for item in categories}
    rows = Product.objects.values("category_id", "status").annotate(total=Count("id"))
    for row in rows:
        for category_id in (row["category_id"], parent_ids.get(row["category_id"])):
            if category_id in totals:
                totals[category_id][0] += row["total"]
                if row["status"] == Product.Status.ON_SALE:
                    totals[category_id][1] += row["total"]
    return totals


def categories_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "catalog.read" if request.method == "GET" else "catalog.write")
    if bad:
        return bad
    if request.method == "GET":
        items = list(Category.objects.all())
        stats = category_totals(items)
        return response(request, [category_data(item, stats) for item in items])
    try:
        values = body(request)
        name = text_field(values.get("name"), "分类名称", 80)
        order = number_field(values.get("sortOrder", 0), "排序", 0, 1000000)
        status = values.get("status", Category.Status.ACTIVE)
        if status not in Category.Status.values:
            raise CatalogError("分类状态不正确。")
        with transaction.atomic():
            parent = category_parent(values.get("parentId"))
            item = Category.objects.create(parent=parent, name=name, sort_order=order, status=status)
            audit(request, "category.create", "category", item.id, actor,
                  after={"name": item.name, "parentId": str(parent.id) if parent else None})
        return response(request, category_data(item, {item.id: (0, 0)}), 201)
    except CatalogError as exc:
        return failure(request, exc)


def category_detail_view(request, category_id):
    bad = method(request, "PATCH")
    if bad:
        return bad
    actor, bad = require(request, "catalog.write")
    if bad:
        return bad
    try:
        values = body(request)
        revision = number_field(values.get("expectedRevision"), "预期修订号", 1)
        if set(values) - {"name", "sortOrder", "status", "expectedRevision"}:
            raise CatalogError("该接口只支持修改名称、排序和状态。")
        with transaction.atomic():
            item = Category.objects.select_for_update().filter(id=category_id).first()
            if not item:
                raise CatalogError("分类不存在。", "NOT_FOUND", 404)
            if item.revision != revision:
                raise CatalogError("分类已被其他人修改。", "REVISION_CONFLICT", 409)
            name = text_field(values.get("name", item.name), "分类名称", 80)
            order = number_field(values.get("sortOrder", item.sort_order), "排序", 0, 1000000)
            status = values.get("status", item.status)
            if status not in Category.Status.values:
                raise CatalogError("分类状态不正确。")
            if status == Category.Status.INACTIVE and item.status == Category.Status.ACTIVE:
                in_use = Product.objects.filter(status=Product.Status.ON_SALE).filter(
                    Q(category=item) | Q(category__parent=item)
                ).exists()
                if in_use:
                    raise CatalogError("请先下架此分类中的在售商品。", "CATEGORY_IN_USE", 409)
            before = {"name": item.name, "sortOrder": item.sort_order, "status": item.status}
            item.name, item.sort_order, item.status = name, order, status
            item.revision += 1
            item.save(update_fields=["name", "sort_order", "status", "revision", "updated_at"])
            audit(request, "category.update", "category", item.id, actor, before=before,
                  after={"name": name, "sortOrder": order, "status": status})
        return response(request, category_data(item, category_totals(list(Category.objects.all()))))
    except CatalogError as exc:
        return failure(request, exc)


def grades_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "catalog.read")
    return bad or response(request, [grade_data(item) for item in MemberGrade.objects.all()])


def sku_rows_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "catalog.read")
    if bad:
        return bad
    try:
        page, size = page_args(request)
        keyword = request.GET.get("keyword", "").strip()
        if len(keyword) > 100:
            raise CatalogError("搜索词过长。")
        rows = Sku.objects.select_related("product", "current_unit").order_by("-created_at", "id")
        if keyword:
            rows = rows.filter(Q(sku_code__icontains=keyword) | Q(product__name__icontains=keyword) |
                               Q(product__product_no__icontains=keyword))
        category_id = request.GET.get("categoryId")
        if category_id:
            identifier = uuid_field(category_id, "分类 ID")
            rows = rows.filter(Q(product__category_id=identifier) | Q(product__category__parent_id=identifier))
        status = request.GET.get("status")
        if status:
            if status not in Sku.SaleStatus.values:
                raise CatalogError("SKU 状态不正确。")
            rows = rows.filter(sale_status=status)
        fulfillment = request.GET.get("fulfillmentKind")
        if fulfillment:
            if fulfillment not in Product.Fulfillment.values:
                raise CatalogError("商品类型不正确。")
            rows = rows.filter(product__fulfillment_kind=fulfillment)
        total = rows.count()
        items = rows[(page - 1) * size:page * size]
        return offset_response(request, {"rows": [sku_data(item) for item in items],
                                  "page": page, "pageSize": size, "total": total})
    except CatalogError as exc:
        return failure(request, exc)


def product_rows_view(request):
    """Admin directory with product pagination and SKU facts as a read projection."""
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "catalog.read")
    if bad:
        return bad
    try:
        page, size = page_args(request)
        keyword = request.GET.get("keyword", "").strip()
        if len(keyword) > 100:
            raise CatalogError("搜索词过长。")
        products = Product.objects.select_related("main_image").order_by("-created_at", "id")
        if keyword:
            code_match = Sku.objects.filter(product_id=OuterRef("pk"), sku_code__icontains=keyword)
            products = products.alias(sku_code_match=Exists(code_match)).filter(
                Q(name__icontains=keyword) | Q(product_no__icontains=keyword) | Q(sku_code_match=True))
        category_id = request.GET.get("categoryId")
        if category_id:
            identifier = uuid_field(category_id, "分类 ID")
            products = products.filter(Q(category_id=identifier) | Q(category__parent_id=identifier))
        fulfillment = request.GET.get("fulfillmentKind")
        if fulfillment:
            if fulfillment not in Product.Fulfillment.values:
                raise CatalogError("商品类型不正确。")
            products = products.filter(fulfillment_kind=fulfillment)
        status = request.GET.get("productStatus")
        if status:
            if status not in Product.Status.values:
                raise CatalogError("商品状态不正确。")
            products = products.filter(status=status)
        sku_status = request.GET.get("skuStatus")
        if sku_status:
            if sku_status not in Sku.SaleStatus.values:
                raise CatalogError("SKU 状态不正确。")
            matching_sku = Sku.objects.filter(product_id=OuterRef("pk"), sale_status=sku_status)
            products = products.alias(sku_status_match=Exists(matching_sku)).filter(sku_status_match=True)
        total = products.count()
        selected = list(products.annotate(
            sku_count=Count("skus"),
            on_sale_sku_count=Count("skus", filter=Q(skus__sale_status=Sku.SaleStatus.ON_SALE)),
            min_list_price_fen=Min("skus__list_price_fen"),
            max_list_price_fen=Max("skus__list_price_fen"),
        )[(page - 1) * size:page * size])
        matched = {}
        if keyword and selected:
            for sku_id, product_id in Sku.objects.filter(
                    product_id__in=[item.id for item in selected], sku_code__icontains=keyword
                    ).order_by("sku_code", "id").values_list("id", "product_id"):
                matched.setdefault(product_id, []).append(str(sku_id))
        rows = [{"productId": str(item.id), "productRevision": item.revision,
                 "productNo": item.product_no, "name": item.name,
                 "categoryId": str(item.category_id), "fulfillmentKind": item.fulfillment_kind,
                 "status": item.status,
                 "mainImage": asset_data(item.main_image) if item.main_image_id else None,
                 "skuCount": item.sku_count, "onSaleSkuCount": item.on_sale_sku_count,
                 "minListPriceFen": item.min_list_price_fen,
                 "maxListPriceFen": item.max_list_price_fen,
                 "matchedSkuIds": matched.get(item.id, [])} for item in selected]
        return offset_response(request, {"rows": rows, "page": page, "pageSize": size, "total": total})
    except CatalogError as exc:
        return failure(request, exc)


def batch_items(values, id_key, extra=()):
    if set(values) - ({"items", "saleStatus" if id_key == "skuId" else "categoryId"} | set(extra)):
        raise CatalogError("批量请求含有不支持的字段。")
    result = []
    seen = set()
    for raw in list_field(values.get("items"), "批量对象", 100, 1):
        item = object_field(raw, "批量对象")
        if set(item) != {id_key, "expectedRevision"}:
            raise CatalogError("批量对象须提供 ID 和预期修订号。")
        identifier = uuid_field(item[id_key], "对象 ID")
        if identifier in seen:
            raise CatalogError("批量请求不能包含重复对象。")
        seen.add(identifier)
        result.append((identifier, number_field(item["expectedRevision"], "预期修订号", 1)))
    return result


def batch_status_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "sku.status.write")
    if bad:
        return bad
    try:
        values = body(request)
        items = batch_items(values, "skuId")
        status = values.get("saleStatus")
        if status not in Sku.SaleStatus.values:
            raise CatalogError("SKU 销售状态不正确。")
        results = []
        for sku_id, revision in items:
            try:
                sku = change_sku_sale_status(request, actor, sku_id, revision, status, "sku.status.batch")
                results.append({"id": str(sku_id), "success": True, "revision": sku.revision,
                                "productStatus": sku.product.status,
                                "productRevision": sku.product.revision})
            except CatalogError as exc:
                results.append({"id": str(sku_id), "success": False,
                                "code": exc.code, "message": str(exc)})
        count = sum(item["success"] for item in results)
        return response(request, {"results": results, "successCount": count,
                                  "failedCount": len(results) - count})
    except CatalogError as exc:
        return failure(request, exc)


def batch_category_preview_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "catalog.write")
    if bad:
        return bad
    try:
        values = body(request)
        by_product = set(values) == {"productIds", "categoryId"}
        if not by_product and set(values) != {"skuIds", "categoryId"}:
            raise CatalogError("请选择商品或 SKU 和目标分类。")
        field = "productIds" if by_product else "skuIds"
        label = "商品 ID" if by_product else "SKU ID"
        identifiers = [uuid_field(value, label) for value in list_field(values[field], label, 100, 1)]
        if len(set(identifiers)) != len(identifiers):
            raise CatalogError(f"不能重复选择同一个{label}。")
        category_id = uuid_field(values["categoryId"], "分类 ID")
        category = Category.objects.select_related("parent").filter(id=category_id).first()
        if not category or not category.parent_id:
            raise CatalogError("请选择二级分类。")
        selected = {}
        ordered_products = []
        if by_product:
            products = {product.id: product for product in Product.objects.filter(id__in=identifiers)}
            if len(products) != len(identifiers):
                raise CatalogError("所选商品已发生变化，请刷新列表。", "SELECTION_STALE", 409)
            ordered_products = [products[identifier] for identifier in identifiers]
        else:
            skus = Sku.objects.select_related("product").filter(id__in=identifiers)
            by_id = {sku.id: sku for sku in skus}
            if len(by_id) != len(identifiers):
                raise CatalogError("所选 SKU 已发生变化，请刷新列表。", "SELECTION_STALE", 409)
            for identifier in identifiers:
                product_id = by_id[identifier].product_id
                if product_id not in selected:
                    selected[product_id] = 0
                    ordered_products.append(by_id[identifier].product)
                selected[product_id] += 1
        if by_product:
            counts = dict(Sku.objects.filter(product_id__in=identifiers).values("product_id")
                          .annotate(total=Count("id")).values_list("product_id", "total"))
            selected = {identifier: counts.get(identifier, 0) for identifier in identifiers}
        else:
            counts = dict(Sku.objects.filter(product_id__in=selected).values("product_id")
                          .annotate(total=Count("id")).values_list("product_id", "total"))
        rows = []
        for product in ordered_products:
            total = counts.get(product.id, 0)
            reason = ("目标分类已停用。" if category.status != Category.Status.ACTIVE or
                      category.parent.status != Category.Status.ACTIVE else
                      "商品已在该分类。" if product.category_id == category.id else None)
            rows.append({"productId": str(product.id), "productNo": product.product_no,
                         "selectedSkuCount": selected[product.id], "totalSkuCount": total,
                         "productRevision": product.revision, "canChange": reason is None,
                         "reason": reason})
        signed_items = [{"productId": row["productId"], "expectedRevision": row["productRevision"]}
                        for row in rows if row["canChange"]]
        token = signing.dumps({"actorId": str(actor.id), "categoryId": str(category.id),
                               field: [str(identifier) for identifier in identifiers],
                               "items": signed_items}, salt="catalog.batch-category", compress=True)
        return response(request, {"previewToken": token, "productCount": len(rows),
                                  "skuCount": sum(counts.values()),
                                  "otherSkuCount": sum(row["totalSkuCount"] - row["selectedSkuCount"] for row in rows),
                                  "items": rows})
    except CatalogError as exc:
        return failure(request, exc)


def batch_category_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "catalog.write")
    if bad:
        return bad
    try:
        values = body(request)
        items = batch_items(values, "productId", extra=("previewToken",))
        category_id = uuid_field(values.get("categoryId"), "分类 ID")
        token = values.get("previewToken")
        if not isinstance(token, str) or not 1 <= len(token) <= 8192:
            raise CatalogError("请先预览批量分类影响。", "PREVIEW_REQUIRED", 409)
        key = uuid_field(request.headers.get("Idempotency-Key"), "Idempotency-Key")
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with transaction.atomic():
            record, created = BatchCategoryRequest.objects.get_or_create(
                actor_id=actor.id, key=key, defaults={"request_digest": digest})
            if not created:
                if record.request_digest != digest:
                    raise CatalogError("同一请求键不能用于不同内容。", "IDEMPOTENCY_CONFLICT", 409)
                if record.result is None:
                    raise CatalogError("批量请求仍在处理，请稍后重试。", "REQUEST_IN_PROGRESS", 409)
                return response(request, record.result)
            try:
                signed = signing.loads(token, salt="catalog.batch-category", max_age=600)
            except signing.BadSignature as exc:
                raise CatalogError("预览已过期或无效，请重新预览。", "PREVIEW_STALE", 409) from exc
            expected = [{"productId": str(product_id), "expectedRevision": revision}
                        for product_id, revision in items]
            if (signed.get("actorId") != str(actor.id) or signed.get("categoryId") != str(category_id) or
                    signed.get("items") != expected):
                raise CatalogError("批量内容与预览不一致，请重新预览。", "PREVIEW_MISMATCH", 409)
            # Lock every product before locking the target category. A single-product
            # edit also locks its product first, so overlapping edits cannot invert
            # the lock order midway through this batch transaction.
            locked_products = {product.id: product for product in Product.objects.filter(
                id__in=[product_id for product_id, _ in items]
            ).order_by("id").select_for_update()}
            results = []
            for product_id, revision in items:
                try:
                    with transaction.atomic():
                        product = locked_products.get(product_id)
                        if not product:
                            raise CatalogError("商品不存在。", "NOT_FOUND", 404)
                        if product.revision != revision:
                            raise CatalogError("商品已被其他人修改。", "REVISION_CONFLICT", 409)
                        category = active_leaf(str(category_id))
                        if product.category_id != category.id:
                            before = {"categoryId": str(product.category_id)}
                            product.category = category
                            product.revision += 1
                            product.save(update_fields=["category", "revision", "updated_at"])
                            audit(request, "product.category.batch", "product", product.id, actor,
                                  before=before, after={"categoryId": str(category.id)})
                        results.append({"id": str(product_id), "success": True,
                                        "revision": product.revision})
                except CatalogError as exc:
                    results.append({"id": str(product_id), "success": False,
                                    "code": exc.code, "message": str(exc)})
            count = sum(item["success"] for item in results)
            result = {"results": results, "successCount": count, "failedCount": len(results) - count}
            record.result = result
            record.save(update_fields=["result"])
        return response(request, result)
    except CatalogError as exc:
        return failure(request, exc)


def products_view(request):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "catalog.write")
    if bad:
        return bad
    for code in ("sku.price.write", "sku.status.write", "sku.unit.write"):
        if code not in permissions(actor):
            _, bad = require(request, code)
            return bad
    try:
        return response(request, create_product(request, actor, body(request)), 201)
    except CatalogError as exc:
        return failure(request, exc)


def product_detail_view(request, product_id):
    bad = method(request, "GET", "PATCH")
    if bad:
        return bad
    actor, bad = require(request, "catalog.read" if request.method == "GET" else "catalog.write")
    if bad:
        return bad
    if request.method == "GET":
        item = Product.objects.select_related("category").filter(id=product_id).first()
        return response(request, product_data(item)) if item else error(request, 404, "NOT_FOUND", "商品不存在。")
    try:
        values = body(request)
        if set(values) - {"name", "categoryId", "fulfillmentKind", "status", "descriptionHtml",
                          "mainImageAssetId", "galleryAssetIds", "videoAssetId", "expectedRevision", "redeemValidUntil"}:
            raise CatalogError("商品基础信息接口不支持修改编号、规格或 SKU。")
        if "status" in values:
            raise CatalogError("商品销售状态由 SKU 状态决定，请在 SKU 行上架或下架。")
        revision = number_field(values.get("expectedRevision"), "预期修订号", 1)
        with transaction.atomic():
            item = Product.objects.select_for_update().filter(id=product_id).first()
            if not item:
                raise CatalogError("商品不存在。", "NOT_FOUND", 404)
            if item.revision != revision:
                raise CatalogError("商品已被其他人修改。", "REVISION_CONFLICT", 409)
            name = text_field(values.get("name", item.name), "商品名称", 120)
            fulfillment = values.get("fulfillmentKind", item.fulfillment_kind)
            if fulfillment not in Product.Fulfillment.values:
                raise CatalogError("履约类型不正确。")
            valid_until = redeem_valid_until(
                values.get("redeemValidUntil", item.redeem_valid_until.isoformat() if item.redeem_valid_until
                           and fulfillment == Product.Fulfillment.REDEEM else None), fulfillment)
            from django.utils import timezone
            if (item.status == Product.Status.ON_SALE and fulfillment == Product.Fulfillment.REDEEM and
                    (valid_until is None or valid_until < timezone.localdate())):
                raise CatalogError("在售核销商品须有尚未到期的核销截止日期。", "REDEEM_VALIDITY_REQUIRED")
            category = active_leaf(values.get("categoryId", str(item.category_id)))
            document = parse_description(values.get("descriptionHtml", item.description_html))
            main_image, gallery, video = resolve_media(values, item, description_ids=document.asset_ids)
            from .asset_access import authorize_asset_binding
            from .media import revalidate_asset_actor
            actor = revalidate_asset_actor(request, "catalog.write", actor)
            authorize_asset_binding(actor, [*document.asset_ids,
                *[asset.id for asset in [main_image, *gallery, video] if asset]],
                [value for value in [item.main_image_id, item.video_id,
                    *item.gallery_images.values_list("asset_id", flat=True),
                    *item.description_images.values_list("asset_id", flat=True)] if value])
            if item.status == Product.Status.ON_SALE and main_image is None:
                raise CatalogError("上架商品须先设置主图。", "MEDIA_REQUIRED")
            before = {"name": item.name, "categoryId": str(item.category_id), "status": item.status,
                      "mainImageAssetId": str(item.main_image_id) if item.main_image_id else None,
                      "galleryAssetIds": [str(row.asset_id) for row in item.gallery_images.order_by("position")],
                      "descriptionImageAssetIds": [str(identifier) for identifier in
                          item.description_images.values_list("asset_id", flat=True)],
                      "videoAssetId": str(item.video_id) if item.video_id else None}
            item.name, item.category, item.fulfillment_kind = name, category, fulfillment
            item.redeem_valid_until = valid_until
            item.description_html = document.html
            item.main_image, item.video = main_image, video
            item.revision += 1
            item.save(update_fields=["name", "category", "fulfillment_kind", "redeem_valid_until",
                                     "description_html", "main_image", "video", "revision", "updated_at"])
            set_description_images(item, document.asset_ids)
            if "galleryAssetIds" in values:
                set_gallery(item, gallery)
            audit(request, "product.update", "product", item.id, actor, before=before,
                  after={"name": name, "categoryId": str(category.id), "status": item.status,
                         "mainImageAssetId": str(main_image.id) if main_image else None,
                         "galleryAssetIds": [str(asset.id) for asset in gallery],
                         "descriptionImageAssetIds": [str(identifier) for identifier in document.asset_ids],
                         "videoAssetId": str(video.id) if video else None})
        return response(request, product_data(item))
    except CatalogError as exc:
        return failure(request, exc)


def product_specs_preview_view(request, product_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "catalog.write")
    if bad:
        return bad
    for code in ("sku.price.write", "sku.status.write", "sku.unit.write"):
        if code not in permissions(actor):
            _, bad = require(request, code)
            return bad
    try:
        return response(request, preview_specs(actor, product_id, body(request)))
    except CatalogError as exc:
        return failure(request, exc)


def product_specs_view(request, product_id):
    bad = method(request, "PUT")
    if bad:
        return bad
    actor, bad = require(request, "catalog.write")
    if bad:
        return bad
    for code in ("sku.price.write", "sku.status.write", "sku.unit.write"):
        if code not in permissions(actor):
            _, bad = require(request, code)
            return bad
    try:
        return response(request, save_specs(request, actor, product_id, body(request)))
    except CatalogError as exc:
        return failure(request, exc)


def sku_change(request, sku_id, operation):
    verbs = {"status": "PATCH", "price": "PATCH", "grade-prices": "PUT", "unit": "PUT"}
    codes = {"status": "sku.status.write", "price": "sku.price.write",
             "grade-prices": "sku.price.write", "unit": "sku.unit.write"}
    bad = method(request, verbs[operation])
    if bad:
        return bad
    actor, bad = require(request, codes[operation])
    if bad:
        return bad
    try:
        values = body(request)
        allowed = {"status": {"saleStatus", "expectedRevision"},
                   "price": {"listPriceFen", "expectedRevision"},
                   "grade-prices": {"gradePrices", "expectedRevision"},
                   "unit": {"unit", "expectedRevision"}}
        if set(values) - allowed[operation]:
            raise CatalogError("SKU 请求含有不支持的字段。")
        revision = number_field(values.get("expectedRevision"), "预期修订号", 1)
        if operation == "status":
            sku = change_sku_sale_status(request, actor, sku_id, revision,
                                         values.get("saleStatus"), "sku.status.update")
            return response(request, sku_data(sku))
        with transaction.atomic():
            sku = Sku.objects.select_for_update(of=("self",)).select_related("product", "current_unit").filter(id=sku_id).first()
            if not sku:
                raise CatalogError("SKU 不存在。", "NOT_FOUND", 404)
            if sku.revision != revision:
                raise CatalogError("SKU 已被其他人修改。", "REVISION_CONFLICT", 409)
            if operation == "price":
                price = number_field(values.get("listPriceFen"), "日常价", 0, 2**53 - 1)
                before, after = {"listPriceFen": sku.list_price_fen}, {"listPriceFen": price}
                sku.list_price_fen = price
                sku.save(update_fields=["list_price_fen", "updated_at"])
            elif operation == "grade-prices":
                grades = set(MemberGrade.objects.filter(enabled=True).values_list("id", flat=True))
                prices = parse_prices(values.get("gradePrices"), grades)
                before = {"gradePrices": [{"gradeId": str(item.grade_id), "priceFen": item.price_fen}
                                          for item in SkuGradePrice.objects.filter(sku=sku, active=True)]}
                # PUT replaces prices for enabled grades only. Disabled grades keep their
                # saved prices so a later grade reactivation does not lose them.
                SkuGradePrice.objects.filter(sku=sku, active=True, grade__enabled=True).update(active=False)
                SkuGradePrice.objects.bulk_create([
                    SkuGradePrice(sku=sku, grade_id=grade_id, price_fen=price) for grade_id, price in prices
                ])
                after = {"gradePrices": [{"gradeId": str(item.grade_id), "priceFen": item.price_fen}
                                         for item in SkuGradePrice.objects.filter(sku=sku, active=True)]}
            else:
                unit = parse_unit(values.get("unit"))
                previous = sku.current_unit
                if (sku.id in referenced_sku_ids([sku.id]) and
                        (not previous or previous.base_unit != unit["base_unit"])):
                    raise CatalogError("SKU 已有关联库存单据或余额，不能修改基础单位。",
                                       "SKU_INVENTORY_REFERENCED", 409)
                try:
                    update_empty_pool_base_unit(sku.id, unit["base_unit"])
                except PoolBindingError as exc:
                    raise CatalogError(str(exc), "POOL_UNIT_MISMATCH", 409) from exc
                before = {"unit": {"baseUnit": previous.base_unit, "saleUnit": previous.sale_unit,
                                   "ratio": previous.ratio} if previous else None}
                version = SkuUnitVersion.objects.create(sku=sku, **unit)
                sku.current_unit = version
                sku.save(update_fields=["current_unit", "updated_at"])
                after = {"unit": {"baseUnit": unit["base_unit"], "saleUnit": unit["sale_unit"],
                                  "ratio": unit["ratio"]}}
            sku.revision += 1
            sku.save(update_fields=["revision", "updated_at"])
            audit(request, f"sku.{operation}.update", "sku", sku.id, actor, before=before, after=after)
        return response(request, sku_data(sku))
    except CatalogError as exc:
        return failure(request, exc)


def sku_status_view(request, sku_id):
    return sku_change(request, sku_id, "status")


def sku_price_view(request, sku_id):
    return sku_change(request, sku_id, "price")


def sku_prices_view(request, sku_id):
    return sku_change(request, sku_id, "grade-prices")


def sku_unit_view(request, sku_id):
    return sku_change(request, sku_id, "unit")


def public_categories_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    visible = list(Category.objects.filter(status=Category.Status.ACTIVE).filter(
        Q(parent__isnull=True) | Q(parent__status=Category.Status.ACTIVE)
    ))
    display_order = lambda item: (item.sort_order, item.name, str(item.id))
    roots = sorted((item for item in visible if item.parent_id is None), key=display_order)
    items = [item for root in roots for item in (
        root, *sorted((child for child in visible if child.parent_id == root.id), key=display_order)
    )]
    return response(request, [category_data(item) for item in items])


def public_products_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    try:
        page, size = page_args(request)
        cheapest_sku = Sku.objects.filter(
            product_id=OuterRef("pk"), sale_status=Sku.SaleStatus.ON_SALE,
        ).order_by("list_price_fen", "created_at", "id")
        from inventory.availability import product_has_available_stock
        products = Product.objects.filter(status=Product.Status.ON_SALE, main_image__isnull=False,
            category__status=Category.Status.ACTIVE,
            category__parent__status=Category.Status.ACTIVE).annotate(
                min_list_price_fen=Subquery(cheapest_sku.values("list_price_fen")[:1]),
                preview_sku_id=Subquery(cheapest_sku.values("id")[:1]),
                has_available_stock=product_has_available_stock(),
            ).filter(min_list_price_fen__isnull=False).order_by("-created_at", "id")
        category_id = request.GET.get("categoryId")
        if category_id:
            identifier = uuid_field(category_id, "分类 ID")
            products = products.filter(Q(category_id=identifier) | Q(category__parent_id=identifier))
        keyword = request.GET.get("keyword", "").strip()
        if len(keyword) > 100:
            raise CatalogError("搜索词过长。")
        if keyword:
            products = products.filter(Q(name__icontains=keyword) | Q(product_no__icontains=keyword))
        total = products.count()
        page_items = list(products[(page - 1) * size:page * size])
        selections = SkuSpecSelection.objects.filter(
            sku_id__in=[item.preview_sku_id for item in page_items],
        ).select_related("axis", "option").order_by("sku_id", "axis__sort_order", "axis_id")
        preview_by_sku = {
            sku_id: " · ".join(f"{selection.axis.name}：{selection.option.value}" for selection in group)
            for sku_id, group in groupby(selections, key=lambda selection: selection.sku_id)
        }
        method_available = bool(enabled_payment_methods())
        rows = [{"productId": str(item.id), "productNo": item.product_no, "name": item.name,
                 "categoryId": str(item.category_id), "fulfillmentKind": item.fulfillment_kind,
                 "redeemValidUntil": item.redeem_valid_until.isoformat() if item.redeem_valid_until else None,
                 "mainImageUrl": f"/api/v1/app/assets/{item.main_image_id}/file",
                 "minListPriceFen": item.min_list_price_fen,
                 "specsPreview": preview_by_sku.get(item.preview_sku_id, "默认规格"),
                 "purchasable": item.has_available_stock and method_available and (
                     item.fulfillment_kind != Product.Fulfillment.REDEEM or
                     bool(item.redeem_valid_until and item.redeem_valid_until >= timezone.localdate())),
                 "cartEligible": item.has_available_stock and (
                     item.fulfillment_kind != Product.Fulfillment.REDEEM or
                     bool(item.redeem_valid_until and item.redeem_valid_until >= timezone.localdate())),
                 "availabilityCode": "REDEEM_VALIDITY_UNAVAILABLE" if item.fulfillment_kind == Product.Fulfillment.REDEEM and
                     (not item.redeem_valid_until or item.redeem_valid_until < timezone.localdate()) else
                     "AVAILABLE" if item.has_available_stock else "STOCK_NOT_READY"}
                for item in page_items]
        return offset_response(request, {"rows": rows, "page": page, "pageSize": size, "total": total})
    except CatalogError as exc:
        return failure(request, exc)


def public_product_detail_view(request, product_id):
    bad = method(request, "GET")
    if bad:
        return bad
    from customers.auth import resolve_member
    member = resolve_member(request)
    if request.headers.get("Authorization") and member is None:
        return error(request, 401, "SESSION_EXPIRED", "登录已失效，请重新登录。")
    item = Product.objects.filter(id=product_id, status=Product.Status.ON_SALE,
        main_image__isnull=False,
        category__status=Category.Status.ACTIVE,
        category__parent__status=Category.Status.ACTIVE).first()
    if not item:
        return error(request, 404, "NOT_FOUND", "商品不可用。")
    if not item.skus.filter(sale_status=Sku.SaleStatus.ON_SALE).exists():
        return error(request, 404, "PRODUCT_OFF_SALE", "商品暂无在售 SKU。")
    return response(request, public_product_data(item, member))
