"""Count a selected SKU scope, review differences, then post immutable adjustments."""

import hashlib
import json
import uuid

from django.db import transaction
from django.utils import timezone

from accounts.models import AdminAccount
from accounts.security import audit
from catalog.inventory_access import lock_stock_skus

from .models import (InventoryBalance, InventoryLedger, StocktakeAction,
                     StocktakeDocument, StocktakeLine, Warehouse)
from .service import MAX_QUANTITY
from .pool_access import resolve_anchor_ids
from .validation import InventoryError, number_field, text_field, uuid_field


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _line_data(line, balance, pending_review):
    current_book = balance.on_hand_base_units if balance else 0
    current_reserved = balance.reserved_base_units if balance else 0
    return {
        "skuId": str(line.sku_id), "skuCode": line.sku.sku_code,
        "productName": line.sku.product.name, "baseUnit": line.base_unit,
        "bookAtStartBaseUnits": line.book_at_start_base_units,
        "bookAtSubmitBaseUnits": line.book_at_submit_base_units,
        "reservedAtSubmitBaseUnits": line.reserved_at_submit_base_units,
        "currentBookBaseUnits": current_book,
        "currentReservedBaseUnits": current_reserved,
        "bookChanged": bool(pending_review and line.book_at_submit_base_units is not None and (
            current_book != line.book_at_submit_base_units or
            current_reserved != line.reserved_at_submit_base_units or
            (balance.updated_at if balance else None) != line.balance_updated_at_at_submit)),
        "countedBaseUnits": line.counted_base_units,
        "deltaBaseUnits": line.delta_base_units, "reason": line.reason,
    }


def stocktake_data(document, include_lines=True):
    data = {
        "stocktakeId": str(document.id), "documentNo": document.document_no,
        "warehouseId": str(document.warehouse_id), "warehouseName": document.warehouse.name,
        "status": document.status, "revision": document.revision,
        "createdAt": document.created_at.isoformat(), "createdBy": document.created_by.display_name,
        "submittedAt": document.submitted_at.isoformat() if document.submitted_at else None,
        "submittedBy": document.submitted_by.display_name if document.submitted_by else None,
        "reviewedAt": document.reviewed_at.isoformat() if document.reviewed_at else None,
        "reviewedBy": document.reviewed_by.display_name if document.reviewed_by else None,
        "reviewNote": document.review_note,
    }
    if include_lines:
        lines = list(document.lines.select_related("sku__product").order_by("sku__sku_code", "sku_id"))
        balances = {row.sku_id: row for row in InventoryBalance.objects.filter(
            warehouse=document.warehouse, sku_id__in=[line.sku_id for line in lines])}
        data["items"] = [_line_data(line, balances.get(line.sku_id),
                                    document.status == StocktakeDocument.Status.PENDING_REVIEW)
                         for line in lines]
        data["itemCount"] = len(lines)
    return data


def _scope(values):
    if not isinstance(values, dict) or set(values) != {"warehouseId", "skuIds"}:
        raise InventoryError("盘点任务字段不正确。")
    warehouse_id = uuid_field(values["warehouseId"], "仓库 ID")
    raw_ids = values["skuIds"]
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 50:
        raise InventoryError("盘点任务须选择 1—50 个 SKU。")
    sku_ids = [uuid_field(item, "SKU ID") for item in raw_ids]
    if len(sku_ids) != len(set(sku_ids)):
        raise InventoryError("盘点任务不能重复选择 SKU。")
    return warehouse_id, sorted(sku_ids)


def create_stocktake(request, actor, values, key):
    warehouse_id, sku_ids = _scope(values)
    digest = _digest({"warehouseId": str(warehouse_id), "skuIds": [str(sku_id) for sku_id in sku_ids]})
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.id)
        existing = (StocktakeDocument.objects.select_related("warehouse", "created_by", "submitted_by", "reviewed_by")
                    .filter(created_by=actor, creation_key=key).first())
        if existing:
            if existing.creation_digest != digest:
                raise InventoryError("请求标识对应的盘点范围不同。", "IDEMPOTENCY_CONFLICT", 409)
            return stocktake_data(existing)
        warehouse = Warehouse.objects.select_for_update().filter(id=warehouse_id, enabled=True).first()
        if not warehouse:
            raise InventoryError("仓库不存在或已停用。", "WAREHOUSE_UNAVAILABLE", 409)
        skus = list(lock_stock_skus(sku_ids))
        if len(skus) != len(sku_ids) or any(not sku.current_unit_id for sku in skus):
            raise InventoryError("SKU 不存在或未配置库存单位。", "SKU_UNIT_NOT_READY", 409)
        anchors = resolve_anchor_ids(sku_ids)
        if any(anchors[sku_id] != sku_id for sku_id in sku_ids):
            raise InventoryError("共享库存只能选择库存池锚 SKU 盘点。", "POOL_ANCHOR_REQUIRED", 409)
        balances = {balance.sku_id: balance for balance in
                    InventoryBalance.objects.select_for_update().filter(
                        warehouse=warehouse, sku_id__in=sku_ids).order_by("sku_id")}
        document_id = uuid.uuid4()
        document = StocktakeDocument.objects.create(
            id=document_id, document_no=f"STK-{document_id.hex[:20].upper()}",
            warehouse=warehouse, created_by=actor, creation_key=key, creation_digest=digest)
        StocktakeLine.objects.bulk_create([
            StocktakeLine(document=document, sku=sku, unit_version=sku.current_unit,
                          base_unit=sku.current_unit.base_unit,
                          book_at_start_base_units=balances[sku.id].on_hand_base_units if sku.id in balances else 0)
            for sku in skus
        ])
        audit(request, "inventory.stocktake.create", "stocktake", document.id, actor,
              after={"warehouseId": str(warehouse.id), "lineCount": len(skus)})
    return stocktake_data(document)


def _lock_document(document_id):
    document = (StocktakeDocument.objects.select_for_update(of=("self",))
                .select_related("warehouse", "created_by", "submitted_by", "reviewed_by")
                .filter(id=document_id).first())
    if not document:
        raise InventoryError("盘点任务不存在。", "NOT_FOUND", 404)
    return document


def _action_replay(document, actor, key, action, digest):
    existing = StocktakeAction.objects.filter(key=key).first()
    if not existing:
        return False
    if (existing.document_id != document.id or existing.actor_id != actor.id or
            existing.action != action or existing.request_digest != digest):
        raise InventoryError("请求标识已用于其他操作。", "IDEMPOTENCY_CONFLICT", 409)
    if document.revision != existing.result_revision:
        raise InventoryError("该请求已执行，但盘点任务此后又发生变化，请刷新任务。", "ACTION_ALREADY_APPLIED", 409)
    return True


def _check_transition(document, expected_revision, status):
    if document.revision != expected_revision:
        raise InventoryError("盘点任务已被修改，请刷新后重试。", "REVISION_CONFLICT", 409)
    if document.status != status:
        raise InventoryError("盘点任务当前状态不可执行此操作。", "STATUS_CONFLICT", 409)
    if not document.warehouse.enabled:
        raise InventoryError("仓库已停用。", "WAREHOUSE_UNAVAILABLE", 409)


def _locked_scope(document):
    lines = list(document.lines.select_related("sku__product", "unit_version").order_by("sku_id"))
    skus = {sku.id: sku for sku in lock_stock_skus([line.sku_id for line in lines])}
    if len(skus) != len(lines) or any(skus[line.sku_id].current_unit_id != line.unit_version_id
                                     for line in lines):
        raise InventoryError("SKU 单位换算已变化，请重新创建盘点任务。", "UNIT_VERSION_CHANGED", 409)
    balances = {balance.sku_id: balance for balance in
                InventoryBalance.objects.select_for_update().filter(
                    warehouse=document.warehouse, sku_id__in=[line.sku_id for line in lines]).order_by("sku_id")}
    return lines, balances


def _count_items(values):
    if not isinstance(values, dict) or set(values) != {"expectedRevision", "items"}:
        raise InventoryError("盘点提交字段不正确。")
    revision = number_field(values["expectedRevision"], "预期修订号", 1, 2**31 - 1)
    raw_items = values["items"]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
        raise InventoryError("实盘明细须包含 1—50 个 SKU。")
    items = {}
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != {"skuId", "countedBaseUnits", "reason"}:
            raise InventoryError("实盘明细字段不正确。")
        sku_id = uuid_field(raw["skuId"], "SKU ID")
        if sku_id in items:
            raise InventoryError("实盘明细不能重复选择 SKU。")
        count = number_field(raw["countedBaseUnits"], "实盘数量", 0, MAX_QUANTITY)
        reason = raw["reason"]
        if not isinstance(reason, str) or len(reason.strip()) > 200:
            raise InventoryError("差异原因最多 200 字符。")
        items[sku_id] = (count, reason.strip())
    return revision, items


def submit_stocktake(request, actor, document_id, values, key):
    revision, items = _count_items(values)
    digest = _digest({"expectedRevision": revision, "items": sorted(
        (str(sku_id), count, reason) for sku_id, (count, reason) in items.items())})
    with transaction.atomic():
        document = _lock_document(document_id)
        if _action_replay(document, actor, key, "SUBMIT", digest):
            return stocktake_data(document)
        _check_transition(document, revision, StocktakeDocument.Status.COUNTING)
        lines, balances = _locked_scope(document)
        if set(items) != {line.sku_id for line in lines}:
            raise InventoryError("请提交盘点任务中的全部 SKU，不能新增或遗漏。")
        for line in lines:
            count, reason = items[line.sku_id]
            balance = balances.get(line.sku_id)
            book = balance.on_hand_base_units if balance else 0
            reserved = balance.reserved_base_units if balance else 0
            delta = count - book
            if delta and not reason:
                raise InventoryError(f"{line.sku.sku_code} 有差异，请填写原因。")
            line.book_at_submit_base_units = book
            line.reserved_at_submit_base_units = reserved
            line.balance_updated_at_at_submit = balance.updated_at if balance else None
            line.counted_base_units = count
            line.delta_base_units = delta
            line.reason = reason
            line.save(update_fields=["book_at_submit_base_units", "reserved_at_submit_base_units",
                                     "balance_updated_at_at_submit",
                                     "counted_base_units", "delta_base_units", "reason"])
        document.status = StocktakeDocument.Status.PENDING_REVIEW
        document.revision += 1
        document.submitted_by = actor
        document.submitted_at = timezone.now()
        document.reviewed_by = None
        document.reviewed_at = None
        document.review_note = ""
        document.save(update_fields=["status", "revision", "submitted_by", "submitted_at",
                                     "reviewed_by", "reviewed_at", "review_note"])
        StocktakeAction.objects.create(document=document, actor=actor, key=key,
                                       action="SUBMIT", request_digest=digest,
                                       result_revision=document.revision)
        audit(request, "inventory.stocktake.submit", "stocktake", document.id, actor,
              after={"lineCount": len(lines), "differenceCount": sum(line.delta_base_units != 0 for line in lines)})
    return stocktake_data(document)


def _review_values(values, action):
    allowed = {"expectedRevision", "reason"} if action == "RETURN" else {"expectedRevision"}
    if not isinstance(values, dict) or set(values) != allowed:
        raise InventoryError("审核字段不正确。")
    revision = number_field(values["expectedRevision"], "预期修订号", 1, 2**31 - 1)
    reason = text_field(values["reason"], "退回原因", 200) if action == "RETURN" else ""
    return revision, reason


def review_stocktake(request, actor, document_id, values, key, action):
    revision, reason = _review_values(values, action)
    digest = _digest({"expectedRevision": revision, "reason": reason, "action": action})
    with transaction.atomic():
        document = _lock_document(document_id)
        if _action_replay(document, actor, key, action, digest):
            return stocktake_data(document)
        _check_transition(document, revision, StocktakeDocument.Status.PENDING_REVIEW)
        before_snapshot = {"revision": document.revision, "status": document.status,
                           "items": [{"skuId": str(line.sku_id),
                                      "bookAtSubmitBaseUnits": line.book_at_submit_base_units,
                                      "countedBaseUnits": line.counted_base_units,
                                      "deltaBaseUnits": line.delta_base_units,
                                      "reason": line.reason}
                                     for line in document.lines.order_by("sku_id")]}
        if action == "APPROVE":
            lines, balances = _locked_scope(document)
            for line in lines:
                balance = balances.get(line.sku_id)
                book = balance.on_hand_base_units if balance else 0
                reserved = balance.reserved_base_units if balance else 0
                if (book != line.book_at_submit_base_units or reserved != line.reserved_at_submit_base_units or
                        (balance.updated_at if balance else None) != line.balance_updated_at_at_submit):
                    raise InventoryError("提交后账面库存已变化，请退回重新盘点。", "BOOK_CHANGED", 409)
                if line.counted_base_units < reserved:
                    raise InventoryError("实盘数量低于已预留库存，请先处理预留。", "RESERVED_STOCK_CONFLICT", 409)
                if line.delta_base_units:
                    if balance is None:
                        balance = InventoryBalance.objects.create(
                            warehouse=document.warehouse, sku_id=line.sku_id,
                            on_hand_base_units=0, reserved_base_units=0)
                        balances[line.sku_id] = balance
                    balance.on_hand_base_units = line.counted_base_units
                    balance.save(update_fields=["on_hand_base_units", "updated_at"])
                    InventoryLedger.objects.create(
                        warehouse=document.warehouse, sku_id=line.sku_id, unit_version=line.unit_version,
                        stocktake_line=line, movement_type=InventoryLedger.MovementType.ADJUSTMENT,
                        operation_unit=line.base_unit, operation_quantity=abs(line.delta_base_units),
                        ratio=1, delta_base_units=line.delta_base_units,
                        balance_before=book, balance_after=line.counted_base_units,
                        reason=line.reason, actor=actor)
            document.status = StocktakeDocument.Status.APPROVED
        else:
            lines = list(document.lines.order_by("sku_id"))
            for line in lines:
                line.book_at_submit_base_units = None
                line.reserved_at_submit_base_units = None
                line.balance_updated_at_at_submit = None
                line.counted_base_units = None
                line.delta_base_units = None
                line.reason = ""
                line.save(update_fields=["book_at_submit_base_units", "reserved_at_submit_base_units",
                                         "balance_updated_at_at_submit",
                                         "counted_base_units", "delta_base_units", "reason"])
            document.status = StocktakeDocument.Status.COUNTING
        document.revision += 1
        document.reviewed_by = actor
        document.reviewed_at = timezone.now()
        document.review_note = reason
        document.save(update_fields=["status", "revision", "reviewed_by", "reviewed_at", "review_note"])
        StocktakeAction.objects.create(document=document, actor=actor, key=key,
                                       action=action, request_digest=digest,
                                       result_revision=document.revision)
        audit(request, "inventory.stocktake." + action.lower(), "stocktake", document.id, actor,
              before=before_snapshot,
              after={"status": document.status, "revision": document.revision,
                     "differenceCount": sum(line.delta_base_units != 0 for line in lines) if action == "APPROVE" else 0,
                     "reason": reason})
    return stocktake_data(document)
