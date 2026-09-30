"""Manual non-sale stock depletion. Every confirmed line has one immutable ledger entry."""

import hashlib
import json
import uuid

from django.db import transaction
from django.utils import timezone

from accounts.models import AdminAccount
from accounts.security import audit
from catalog.inventory_access import lock_stock_skus

from .models import InventoryBalance, InventoryLedger, OutboundDocument, OutboundLine, Warehouse
from .pool_access import resolve_anchor_ids
from .service import MAX_QUANTITY, line_data
from .validation import InventoryError, number_field, uuid_field


def outbound_data(document, include_lines=True):
    data = {"outboundId": str(document.id), "documentNo": document.document_no,
            "warehouseId": str(document.warehouse_id), "warehouseName": document.warehouse.name,
            "status": document.status, "revision": document.revision, "reason": document.reason,
            "note": document.note, "createdAt": document.created_at.isoformat(),
            "createdBy": document.created_by.display_name,
            "confirmedAt": document.confirmed_at.isoformat() if document.confirmed_at else None}
    if include_lines:
        lines = list(document.lines.select_related("sku__product", "unit_version").order_by("sku__sku_code"))
        data["items"] = [line_data(line) for line in lines]
        data["itemCount"] = len(lines)
        data["totalBaseUnits"] = sum(line.base_quantity for line in lines)
    return data


def _planned_items(values):
    if not isinstance(values, dict) or set(values) != {"warehouseId", "reason", "note", "items"}:
        raise InventoryError("出库单字段不正确。")
    warehouse_id = uuid_field(values["warehouseId"], "仓库 ID")
    reason = values["reason"]
    if reason not in OutboundDocument.Reason.values:
        raise InventoryError("出库原因不正确。")
    note = values["note"]
    if not isinstance(note, str) or len(note.strip()) > 200:
        raise InventoryError("备注最多 200 字符。")
    note = note.strip()
    raw_items = values["items"]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
        raise InventoryError("出库单须包含 1—50 个 SKU。")
    planned, seen = [], set()
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != {"skuId", "quantity", "unit"}:
            raise InventoryError("出库明细字段不正确。")
        sku_id = uuid_field(raw["skuId"], "SKU ID")
        if sku_id in seen:
            raise InventoryError("同一出库单不能重复选择 SKU。")
        seen.add(sku_id)
        quantity = number_field(raw["quantity"], "出库数量", 1, MAX_QUANTITY)
        if raw["unit"] not in ("BASE", "SALE"):
            raise InventoryError("操作单位不正确。")
        planned.append((sku_id, quantity, raw["unit"]))
    return warehouse_id, reason, note, planned


def create_outbound(request, actor, values, key):
    warehouse_id, reason, note, planned = _planned_items(values)
    digest_source = {"warehouseId": str(warehouse_id), "reason": reason, "note": note,
                     "items": sorted((str(sku_id), quantity, unit) for sku_id, quantity, unit in planned)}
    digest = hashlib.sha256(json.dumps(digest_source, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.id)
        existing = (OutboundDocument.objects.select_related("warehouse", "created_by")
                    .filter(created_by=actor, creation_key=key).first())
        if existing:
            if existing.creation_digest != digest:
                raise InventoryError("请求标识对应的出库内容不同。", "IDEMPOTENCY_CONFLICT", 409)
            return outbound_data(existing)
        warehouse = Warehouse.objects.select_for_update().filter(id=warehouse_id, enabled=True).first()
        if not warehouse:
            raise InventoryError("仓库不存在或已停用。", "WAREHOUSE_UNAVAILABLE", 409)
        skus = {sku.id: sku for sku in lock_stock_skus([item[0] for item in planned])}
        if len(skus) != len(planned) or any(not sku.current_unit_id for sku in skus.values()):
            raise InventoryError("SKU 不存在或未配置库存单位。", "SKU_UNIT_NOT_READY", 409)
        document_id = uuid.uuid4()
        document = OutboundDocument.objects.create(
            id=document_id, document_no=f"OUT-{document_id.hex[:20].upper()}",
            warehouse=warehouse, reason=reason, note=note, created_by=actor,
            creation_key=key, creation_digest=digest)
        for sku_id, quantity, unit in planned:
            version = skus[sku_id].current_unit
            ratio = version.ratio if unit == "SALE" else 1
            if quantity > MAX_QUANTITY // ratio:
                raise InventoryError("换算后的出库数量过大。")
            OutboundLine.objects.create(document=document, sku_id=sku_id, unit_version=version,
                                        quantity=quantity, operation_unit=(version.sale_unit if unit == "SALE"
                                                                             else version.base_unit),
                                        ratio=ratio, base_quantity=quantity * ratio,
                                        base_unit=version.base_unit)
        audit(request, "inventory.outbound.draft", "outbound", document.id, actor,
              after={"warehouseId": str(warehouse.id), "lineCount": len(planned), "reason": reason})
    return outbound_data(document)


def confirm_outbound(request, actor, document_id, expected_revision, key):
    with transaction.atomic():
        document = (OutboundDocument.objects.select_for_update(of=("self",))
                    .select_related("warehouse", "created_by").filter(id=document_id).first())
        if not document:
            raise InventoryError("出库单不存在。", "NOT_FOUND", 404)
        if document.status == OutboundDocument.Status.CONFIRMED:
            if document.confirmation_key == key and document.confirmed_by_id == actor.id:
                return outbound_data(document)
            raise InventoryError("出库单已确认。", "ALREADY_CONFIRMED", 409)
        if document.revision != expected_revision:
            raise InventoryError("出库单已被修改。", "REVISION_CONFLICT", 409)
        if not document.warehouse.enabled:
            raise InventoryError("仓库已停用。", "WAREHOUSE_UNAVAILABLE", 409)
        if OutboundDocument.objects.filter(confirmation_key=key).exists():
            raise InventoryError("请求标识已被其他出库单使用。", "IDEMPOTENCY_CONFLICT", 409)
        lines = list(document.lines.select_related("sku", "unit_version").order_by("sku_id"))
        skus = {sku.id: sku for sku in lock_stock_skus([line.sku_id for line in lines])}
        if any(skus[line.sku_id].current_unit_id != line.unit_version_id for line in lines):
            raise InventoryError("SKU 单位换算已变化，请重新创建出库单。", "UNIT_VERSION_CHANGED", 409)
        anchors = resolve_anchor_ids([line.sku_id for line in lines])
        balances = {balance.sku_id: balance for balance in InventoryBalance.objects.select_for_update().filter(
            warehouse=document.warehouse, sku_id__in=set(anchors.values())).order_by("sku_id")}
        requested = {}
        for line in lines:
            anchor = anchors[line.sku_id]
            requested[anchor] = requested.get(anchor, 0) + line.base_quantity
        if any(anchor not in balances or
               balances[anchor].on_hand_base_units - balances[anchor].reserved_base_units < quantity
               for anchor, quantity in requested.items()):
            raise InventoryError("可售库存不足，出库单未确认。", "INSUFFICIENT_STOCK", 409)
        for line in lines:
            balance = balances[anchors[line.sku_id]]
            available = (balance.on_hand_base_units - balance.reserved_base_units) if balance else 0
            if available < line.base_quantity:
                raise InventoryError("可售库存不足，出库单未确认。", "INSUFFICIENT_STOCK", 409)
            before = balance.on_hand_base_units
            balance.on_hand_base_units = before - line.base_quantity
            balance.save(update_fields=["on_hand_base_units", "updated_at"])
            InventoryLedger.objects.create(
                warehouse=document.warehouse, sku_id=line.sku_id, unit_version=line.unit_version,
                outbound_line=line, movement_type=InventoryLedger.MovementType.OUTBOUND,
                operation_unit=line.operation_unit, operation_quantity=line.quantity, ratio=line.ratio,
                delta_base_units=-line.base_quantity, balance_before=before,
                balance_after=balance.on_hand_base_units, reason=document.reason,
                note=document.note, actor=actor)
        document.status = OutboundDocument.Status.CONFIRMED
        document.revision += 1
        document.confirmation_key = key
        document.confirmed_by = actor
        document.confirmed_at = timezone.now()
        document.save(update_fields=["status", "revision", "confirmation_key", "confirmed_by", "confirmed_at"])
        audit(request, "inventory.outbound.confirm", "outbound", document.id, actor,
              after={"warehouseId": str(document.warehouse_id), "lineCount": len(lines),
                     "reason": document.reason})
    return outbound_data(document)
