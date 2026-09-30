"""Authenticated inventory API; no catalog pricing data is exposed here."""

import uuid

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum

from common.http import offset_response, method
from accounts.security import error, parse_json, require, response
from accounts.security import audit
from catalog.models import Product, Sku
from .selection import selection_page
from .pool_access import (PoolBindingError, bind_sku_to_pool, ensure_independent_pool, pool_info_for_skus,
                          resolve_anchor_id)

from .models import InboundDocument, InventoryBalance, InventoryLedger, OutboundDocument, StockPool, StockPoolSku, StocktakeDocument, Warehouse
from .outbound_service import confirm_outbound, create_outbound, outbound_data
from .service import (InventoryError, confirm_inbound, create_inbound,
                      create_warehouse, inbound_data, warehouse_data)
from .validation import uuid_field
from .stocktake_service import create_stocktake, review_stocktake, stocktake_data, submit_stocktake


def pool_product_options_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    keyword = request.GET.get("keyword", "").strip()
    if len(keyword) > 100:
        return error(request, 400, "VALIDATION_FAILED", "搜索词过长。")
    products = Product.objects.order_by("-created_at", "id")
    if keyword:
        sku_match = Sku.objects.filter(product_id=OuterRef("pk"), sku_code__icontains=keyword)
        products = products.alias(sku_match=Exists(sku_match)).filter(
            Q(name__icontains=keyword) | Q(product_no__icontains=keyword) | Q(sku_match=True))
    rows = products.annotate(sku_count=Count("skus"))[:20]
    return response(request, {"items": [{"productId": str(row.id), "productNo": row.product_no,
                                         "name": row.name, "skuCount": row.sku_count} for row in rows]})


def pool_bindings_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.read" if request.method == "GET" else "inventory.manage")
    if bad:
        return bad
    try:
        if request.method == "GET":
            product_id = uuid_field(request.GET.get("productId"), "商品 ID")
            rows = list(Sku.objects.filter(product_id=product_id).select_related("current_unit")
                        .order_by("sku_code", "id"))
            info = pool_info_for_skus([row.id for row in rows])
            anchors = {row.id: row.sku_code for row in rows}
            return response(request, {"items": [
                {"skuId": str(row.id), "skuCode": row.sku_code, "skuRevision": row.revision,
                 "baseUnit": row.current_unit.base_unit if row.current_unit else None,
                 **info[row.id], "poolAnchorSkuCode": anchors.get(
                     uuid.UUID(info[row.id]["anchorSkuId"]))}
                for row in rows]})
        values = body(request)
        if not isinstance(values, dict) or (set(values) not in (
                {"skuId", "poolId", "expectedSkuRevision"},
                {"skuId", "anchorSkuId", "expectedSkuRevision"})):
            raise InventoryError("库存池绑定字段不正确。")
        sku_id = uuid_field(values["skuId"], "SKU ID")
        revision = values["expectedSkuRevision"]
        if type(revision) is not int or revision < 1:
            raise InventoryError("SKU 修订号不正确。")
        with transaction.atomic():
            if "anchorSkuId" in values:
                anchor_id = uuid_field(values["anchorSkuId"], "锚 SKU ID")
                locked = {row.id: row for row in Sku.objects.select_for_update(of=("self",))
                          .select_related("current_unit").filter(pk__in=[sku_id, anchor_id]).order_by("id")}
                anchor = locked.get(anchor_id)
                if anchor is None:
                    raise Sku.DoesNotExist
                was_pool_present = StockPool.objects.filter(anchor_sku_id=anchor_id).exists()
                target = ensure_independent_pool(anchor)
                if target.anchor_sku_id != anchor_id:
                    raise PoolBindingError("锚 SKU 已加入其他库存池。")
                if not was_pool_present:
                    audit(request, "inventory.pool.create", "sku", anchor_id, actor,
                          after={"poolId": str(target.id), "baseUnit": target.base_unit})
                pool_id = target.id
            else:
                pool_id = uuid_field(values["poolId"], "库存池 ID")
            pool, changed = bind_sku_to_pool(sku_id, pool_id, revision)
            if changed:
                audit(request, "inventory.pool.bind", "sku", sku_id, actor,
                      after={"poolId": str(pool.id), "anchorSkuId": str(pool.anchor_sku_id)})
        return response(request, {"skuId": str(sku_id), "poolId": str(pool.id),
                                  "anchorSkuId": str(pool.anchor_sku_id), "changed": changed})
    except (Sku.DoesNotExist, StockPool.DoesNotExist):
        return error(request, 404, "NOT_FOUND", "SKU 或库存池不存在。")
    except PoolBindingError as exc:
        return error(request, 409, "POOL_BINDING_CONFLICT", str(exc))
    except InventoryError as exc:
        return failure(request, exc)


def failure(request, exc):
    return error(request, exc.status, exc.code, str(exc))


def body(request):
    try:
        return parse_json(request)
    except ValueError as exc:
        raise InventoryError(str(exc)) from exc


def request_key(request):
    raw_key = request.headers.get("Idempotency-Key", "")
    try:
        return uuid.UUID(raw_key)
    except (ValueError, AttributeError) as exc:
        raise InventoryError("请提供 UUID 格式的 Idempotency-Key。") from exc


def page_args(request):
    try:
        page = int(request.GET.get("page", "1"))
        size = int(request.GET.get("pageSize", "20"))
    except ValueError as exc:
        raise InventoryError("分页参数不正确。") from exc
    if not 1 <= page <= 100000 or not 1 <= size <= 100:
        raise InventoryError("分页参数不正确。")
    return page, size


def warehouses_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.read" if request.method == "GET" else "inventory.manage")
    if bad:
        return bad
    if request.method == "GET":
        rows = Warehouse.objects.order_by("-is_default", "code")
        return response(request, {"items": [warehouse_data(row) for row in rows]})
    try:
        return response(request, create_warehouse(request, actor, body(request)), status=201)
    except InventoryError as exc:
        return failure(request, exc)
    except IntegrityError:
        return error(request, 409, "WAREHOUSE_CONFLICT", "仓库编码或默认仓库已存在。")


def skus_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    try:
        page, size = page_args(request)
        keyword = request.GET.get("keyword", "").strip()
        if len(keyword) > 120:
            raise InventoryError("搜索词过长。")
        return offset_response(request, selection_page(
            keyword, page, size, request.GET.get("warehouseId")))
    except InventoryError as exc:
        return failure(request, exc)


def balances_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    try:
        page, size = page_args(request)
        keyword = request.GET.get("keyword", "").strip()
        if len(keyword) > 120:
            raise InventoryError("搜索词过长。")
        rows = InventoryBalance.objects.select_related("warehouse", "sku__product", "sku__current_unit")
        if request.GET.get("warehouseId"):
            rows = rows.filter(warehouse_id=uuid_field(request.GET["warehouseId"], "仓库 ID"))
        if request.GET.get("skuId"):
            rows = rows.filter(sku_id=resolve_anchor_id(uuid_field(request.GET["skuId"], "SKU ID")))
        if keyword:
            alias_anchors = StockPoolSku.objects.filter(
                sku__sku_code__icontains=keyword).values_list("pool__anchor_sku_id", flat=True)
            rows = rows.filter(Q(sku__sku_code__icontains=keyword) |
                               Q(sku__product__name__icontains=keyword) |
                               Q(sku_id__in=alias_anchors))
        total = rows.count()
        rows = rows.order_by("warehouse__code", "sku__sku_code", "id")[(page - 1) * size:page * size]
        infos = pool_info_for_skus([row.sku_id for row in rows])
        member_codes = {}
        for member in StockPoolSku.objects.filter(pool__anchor_sku_id__in=[row.sku_id for row in rows]).select_related("sku", "pool"):
            member_codes.setdefault(member.pool.anchor_sku_id, []).append(member.sku.sku_code)
        items = [{"warehouseId": str(row.warehouse_id), "warehouseName": row.warehouse.name,
                  "skuId": str(row.sku_id), "skuCode": row.sku.sku_code,
                  "productName": row.sku.product.name,
                  **infos[row.sku_id],
                  "poolAnchorSkuCode": row.sku.sku_code,
                  "poolSkuCodes": sorted(member_codes.get(row.sku_id, [row.sku.sku_code])),
                  "baseUnit": row.sku.current_unit.base_unit if row.sku.current_unit else "",
                  "onHandBaseUnits": row.on_hand_base_units,
                  "reservedBaseUnits": row.reserved_base_units,
                  "availableBaseUnits": row.on_hand_base_units - row.reserved_base_units}
                 for row in rows]
        return offset_response(request, {"items": items, "page": page, "pageSize": size, "total": total})
    except (InventoryError, ValueError) as exc:
        return failure(request, exc if isinstance(exc, InventoryError) else InventoryError(str(exc)))


def inbounds_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.read" if request.method == "GET" else "inventory.manage")
    if bad:
        return bad
    try:
        if request.method == "POST":
            return response(request, create_inbound(request, actor, body(request), request_key(request)), status=201)
        page, size = page_args(request)
        rows = (InboundDocument.objects.select_related("warehouse", "created_by")
                .annotate(line_count=Count("lines"), line_total=Sum("lines__base_quantity")))
        if request.GET.get("warehouseId"):
            rows = rows.filter(warehouse_id=uuid_field(request.GET["warehouseId"], "仓库 ID"))
        if request.GET.get("status"):
            status = request.GET["status"]
            if status not in InboundDocument.Status.values:
                raise InventoryError("入库单状态不正确。")
            rows = rows.filter(status=status)
        total = rows.count()
        rows = rows.order_by("-created_at", "id")[(page - 1) * size:page * size]
        items = []
        for row in rows:
            item = inbound_data(row, include_lines=False)
            item.update(itemCount=row.line_count, totalBaseUnits=row.line_total or 0)
            items.append(item)
        return offset_response(request, {"items": items, "page": page, "pageSize": size, "total": total})
    except InventoryError as exc:
        return failure(request, exc)


def inbound_detail_view(request, inbound_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    document = (InboundDocument.objects.select_related("warehouse", "created_by")
                .filter(id=inbound_id).first())
    if not document:
        return error(request, 404, "NOT_FOUND", "入库单不存在。")
    return response(request, inbound_data(document))


def inbound_confirm_view(request, inbound_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.manage")
    if bad:
        return bad
    try:
        values = body(request)
        if (not isinstance(values, dict) or set(values) != {"expectedRevision"}
                or type(values["expectedRevision"]) is not int or values["expectedRevision"] < 1):
            raise InventoryError("预期修订号不正确。")
        key = request_key(request)
        return response(request, confirm_inbound(request, actor, inbound_id,
                                                 values["expectedRevision"], key))
    except InventoryError as exc:
        return failure(request, exc)
    except IntegrityError:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已被使用，请查询入库单状态。")


def outbounds_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.read" if request.method == "GET" else "inventory.manage")
    if bad:
        return bad
    try:
        if request.method == "POST":
            return response(request, create_outbound(request, actor, body(request), request_key(request)), status=201)
        page, size = page_args(request)
        rows = (OutboundDocument.objects.select_related("warehouse", "created_by")
                .annotate(line_count=Count("lines"), line_total=Sum("lines__base_quantity")))
        if request.GET.get("warehouseId"):
            rows = rows.filter(warehouse_id=uuid_field(request.GET["warehouseId"], "仓库 ID"))
        if request.GET.get("status"):
            status = request.GET["status"]
            if status not in OutboundDocument.Status.values:
                raise InventoryError("出库单状态不正确。")
            rows = rows.filter(status=status)
        total = rows.count()
        rows = rows.order_by("-created_at", "id")[(page - 1) * size:page * size]
        items = []
        for row in rows:
            item = outbound_data(row, include_lines=False)
            item.update(itemCount=row.line_count, totalBaseUnits=row.line_total or 0)
            items.append(item)
        return offset_response(request, {"items": items, "page": page, "pageSize": size, "total": total})
    except InventoryError as exc:
        return failure(request, exc)
    except IntegrityError:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已被使用，请查询出库单状态。")


def outbound_detail_view(request, outbound_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    document = (OutboundDocument.objects.select_related("warehouse", "created_by")
                .filter(id=outbound_id).first())
    if not document:
        return error(request, 404, "NOT_FOUND", "出库单不存在。")
    return response(request, outbound_data(document))


def outbound_confirm_view(request, outbound_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.manage")
    if bad:
        return bad
    try:
        values = body(request)
        if (not isinstance(values, dict) or set(values) != {"expectedRevision"} or type(values["expectedRevision"]) is not int
                or values["expectedRevision"] < 1):
            raise InventoryError("预期修订号不正确。")
        return response(request, confirm_outbound(request, actor, outbound_id,
                                                  values["expectedRevision"], request_key(request)))
    except InventoryError as exc:
        return failure(request, exc)
    except IntegrityError:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已被使用，请查询出库单状态。")


def stocktakes_view(request):
    bad = method(request, "GET", "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.read" if request.method == "GET" else "inventory.manage")
    if bad:
        return bad
    try:
        if request.method == "POST":
            return response(request, create_stocktake(request, actor, body(request), request_key(request)), status=201)
        page, size = page_args(request)
        rows = (StocktakeDocument.objects.select_related("warehouse", "created_by", "submitted_by", "reviewed_by")
                .annotate(item_count=Count("lines")))
        if request.GET.get("warehouseId"):
            rows = rows.filter(warehouse_id=uuid_field(request.GET["warehouseId"], "仓库 ID"))
        if request.GET.get("status"):
            status = request.GET["status"]
            if status not in StocktakeDocument.Status.values:
                raise InventoryError("盘点状态不正确。")
            rows = rows.filter(status=status)
        total = rows.count()
        rows = rows.order_by("-created_at", "id")[(page - 1) * size:page * size]
        items = []
        for row in rows:
            item = stocktake_data(row, include_lines=False)
            item["itemCount"] = row.item_count
            items.append(item)
        return offset_response(request, {"items": items, "page": page, "pageSize": size, "total": total})
    except InventoryError as exc:
        return failure(request, exc)
    except IntegrityError:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已被使用，请查询盘点任务状态。")


def stocktake_detail_view(request, stocktake_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    document = (StocktakeDocument.objects.select_related("warehouse", "created_by", "submitted_by", "reviewed_by")
                .filter(id=stocktake_id).first())
    if not document:
        return error(request, 404, "NOT_FOUND", "盘点任务不存在。")
    return response(request, stocktake_data(document))


def stocktake_submit_view(request, stocktake_id):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.manage")
    if bad:
        return bad
    try:
        return response(request, submit_stocktake(request, actor, stocktake_id,
                                                  body(request), request_key(request)))
    except InventoryError as exc:
        return failure(request, exc)
    except IntegrityError:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已被使用，请查询盘点任务状态。")


def _review_view(request, stocktake_id, action):
    bad = method(request, "POST")
    if bad:
        return bad
    actor, bad = require(request, "inventory.review")
    if bad:
        return bad
    try:
        return response(request, review_stocktake(request, actor, stocktake_id,
                                                  body(request), request_key(request), action))
    except InventoryError as exc:
        return failure(request, exc)
    except IntegrityError:
        return error(request, 409, "IDEMPOTENCY_CONFLICT", "请求标识已被使用，请查询盘点任务状态。")


def stocktake_return_view(request, stocktake_id):
    return _review_view(request, stocktake_id, "RETURN")


def stocktake_approve_view(request, stocktake_id):
    return _review_view(request, stocktake_id, "APPROVE")


def ledger_data(row, include_version=False):
    if row.movement_type == "SALE":
        source_line = row.order_line
        document = source_line.order
        document_no = document.order_no
        base_unit = source_line.base_unit
    else:
        source_line = (row.inbound_line if row.movement_type == "INBOUND" else
                       row.outbound_line if row.movement_type == "OUTBOUND" else row.stocktake_line)
        document = source_line.document
        document_no = document.document_no
        base_unit = source_line.base_unit
    data = {"ledgerId": str(row.id), "movementType": row.movement_type,
            "warehouseId": str(row.warehouse_id), "warehouseName": row.warehouse.name,
            "skuId": str(row.sku_id), "skuCode": row.sku.sku_code,
            "productName": row.sku.product.name, "documentNo": document_no,
            "documentId": str(document.id), "operationUnit": row.operation_unit,
            "operationQuantity": row.operation_quantity, "ratio": row.ratio,
            "baseUnit": base_unit,
            "deltaBaseUnits": row.delta_base_units, "balanceBefore": row.balance_before,
            "balanceAfter": row.balance_after, "reason": row.reason, "note": row.note,
            "actorName": row.actor.display_name if row.actor else "系统",
            "occurredAt": row.occurred_at.isoformat()}
    if include_version:
        data["unitVersionId"] = str(row.unit_version_id)
    return data


def _ledger_rows():
    return InventoryLedger.objects.select_related("warehouse", "sku__product", "actor",
                                                  "inbound_line__document", "outbound_line__document",
                                                  "stocktake_line__document", "order_line__order")


def ledgers_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    try:
        page, size = page_args(request)
        rows = _ledger_rows()
        if request.GET.get("warehouseId"):
            rows = rows.filter(warehouse_id=uuid_field(request.GET["warehouseId"], "仓库 ID"))
        if request.GET.get("skuId"):
            rows = rows.filter(sku_id=uuid_field(request.GET["skuId"], "SKU ID"))
        if request.GET.get("movementType"):
            movement_type = request.GET["movementType"]
            if movement_type not in InventoryLedger.MovementType.values:
                raise InventoryError("库存流水类型不正确。")
            rows = rows.filter(movement_type=movement_type)
        keyword = request.GET.get("keyword", "").strip()
        if len(keyword) > 120:
            raise InventoryError("搜索词过长。")
        if keyword:
            rows = rows.filter(Q(sku__sku_code__icontains=keyword) |
                               Q(sku__product__name__icontains=keyword) |
                               Q(inbound_line__document__document_no__icontains=keyword) |
                               Q(outbound_line__document__document_no__icontains=keyword) |
                               Q(stocktake_line__document__document_no__icontains=keyword) |
                               Q(order_line__order__order_no__icontains=keyword))
        total = rows.count()
        rows = rows.order_by("-occurred_at", "-id")[(page - 1) * size:page * size]
        return offset_response(request, {"items": [ledger_data(row) for row in rows],
                                  "page": page, "pageSize": size, "total": total})
    except InventoryError as exc:
        return failure(request, exc)


def ledger_detail_view(request, ledger_id):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "inventory.read")
    if bad:
        return bad
    row = _ledger_rows().filter(id=ledger_id).first()
    if not row:
        return error(request, 404, "NOT_FOUND", "库存流水不存在。")
    return response(request, ledger_data(row, include_version=True))
