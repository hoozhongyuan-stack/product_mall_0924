"""Inventory writes and their database transaction boundaries."""

import hashlib
import json
import uuid

from django.db import transaction
from django.utils import timezone

from accounts.security import audit
from accounts.models import AdminAccount
from catalog.inventory_access import lock_stock_skus

from .models import InboundDocument, InboundLine, InventoryBalance, InventoryLedger, Warehouse
from .pool_access import ensure_independent_pool, resolve_anchor_ids
from .validation import InventoryError, code_field, number_field, text_field, uuid_field


# JSON numbers must remain exact in the native mini-program and PC client.
MAX_QUANTITY = 2**53 - 1


def warehouse_data(warehouse):
    return {"warehouseId": str(warehouse.id), "code": warehouse.code, "name": warehouse.name,
            "isDefault": warehouse.is_default, "enabled": warehouse.enabled,
            "revision": warehouse.revision}


def line_data(line):
    sku = line.sku
    return {"skuId": str(sku.id), "skuCode": sku.sku_code, "productName": sku.product.name,
            "quantity": line.quantity, "operationUnit": line.operation_unit,
            "ratio": line.ratio, "baseQuantity": line.base_quantity, "baseUnit": line.base_unit,
            "unitVersionId": str(line.unit_version_id)}


def inbound_data(document, include_lines=True):
    data = {"inboundId": str(document.id), "documentNo": document.document_no,
            "warehouseId": str(document.warehouse_id), "warehouseName": document.warehouse.name,
            "status": document.status, "revision": document.revision, "reason": document.reason,
            "createdAt": document.created_at.isoformat(), "createdBy": document.created_by.display_name,
            "confirmedAt": document.confirmed_at.isoformat() if document.confirmed_at else None}
    if include_lines:
        lines = list(document.lines.select_related("sku__product", "unit_version").order_by("sku__sku_code"))
        data["items"] = [line_data(line) for line in lines]
        data["itemCount"] = len(lines)
        data["totalBaseUnits"] = sum(line.base_quantity for line in lines)
    return data


def create_warehouse(request, actor, values):
    if not isinstance(values, dict) or set(values) != {"code", "name", "isDefault"}:
        raise InventoryError("仓库字段不正确。")
    code = code_field(values.get("code"), "仓库编码")
    name = text_field(values.get("name"), "仓库名称", 120)
    is_default = values.get("isDefault")
    if type(is_default) is not bool:
        raise InventoryError("默认仓库标记不正确。")
    with transaction.atomic():
        existing = Warehouse.objects.select_for_update().exists()
        if not existing and not is_default:
            raise InventoryError("首个仓库必须设为默认仓。")
        if existing and is_default:
            raise InventoryError("已有默认仓；当前切片不支持切换默认仓。", "DEFAULT_WAREHOUSE_EXISTS", 409)
        warehouse = Warehouse.objects.create(code=code, name=name, is_default=is_default)
        audit(request, "inventory.warehouse.create", "warehouse", warehouse.id, actor,
              after={"code": warehouse.code, "name": warehouse.name,
                     "isDefault": warehouse.is_default})
    return warehouse_data(warehouse)


def create_inbound(request, actor, values, key):
    if not isinstance(values, dict) or set(values) != {"warehouseId", "reason", "items"}:
        raise InventoryError("入库单字段不正确。")
    warehouse_id = uuid_field(values.get("warehouseId"), "仓库 ID")
    reason = text_field(values.get("reason"), "入库原因", 200)
    raw_items = values.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 50:
        raise InventoryError("入库单须包含 1—50 个 SKU。")
    planned, seen = [], set()
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != {"skuId", "quantity", "unit"}:
            raise InventoryError("入库明细字段不正确。")
        sku_id = uuid_field(raw["skuId"], "SKU ID")
        if sku_id in seen:
            raise InventoryError("同一入库单不能重复选择 SKU。")
        seen.add(sku_id)
        quantity = number_field(raw["quantity"], "入库数量", 1, MAX_QUANTITY)
        if raw["unit"] not in ("BASE", "SALE"):
            raise InventoryError("操作单位不正确。")
        planned.append((sku_id, quantity, raw["unit"]))
    digest_source = {"warehouseId": str(warehouse_id), "reason": reason,
                     "items": sorted([(str(sku_id), quantity, unit)
                                      for sku_id, quantity, unit in planned])}
    digest = hashlib.sha256(json.dumps(digest_source, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    with transaction.atomic():
        # Serialize this actor's draft requests so a lost HTTP response can be
        # retried with the same key without producing a second confirmable slip.
        AdminAccount.objects.select_for_update().get(pk=actor.id)
        existing = (InboundDocument.objects.select_related("warehouse", "created_by")
                    .filter(created_by=actor, creation_key=key).first())
        if existing:
            if existing.creation_digest != digest:
                raise InventoryError("请求标识对应的入库内容不同。", "IDEMPOTENCY_CONFLICT", 409)
            return inbound_data(existing)
        warehouse = Warehouse.objects.select_for_update().filter(id=warehouse_id, enabled=True).first()
        if not warehouse:
            raise InventoryError("仓库不存在或已停用。", "WAREHOUSE_UNAVAILABLE", 409)
        skus = {sku.id: sku for sku in lock_stock_skus(seen)}
        if len(skus) != len(seen) or any(not sku.current_unit_id for sku in skus.values()):
            raise InventoryError("SKU 不存在或未配置库存单位。", "SKU_UNIT_NOT_READY", 409)
        document_id = uuid.uuid4()
        document = InboundDocument.objects.create(
            id=document_id, document_no=f"IN-{document_id.hex[:20].upper()}",
            warehouse=warehouse, reason=reason, created_by=actor,
            creation_key=key, creation_digest=digest)
        for sku_id, quantity, unit in planned:
            sku = skus[sku_id]
            version = sku.current_unit
            ratio = version.ratio if unit == "SALE" else 1
            if quantity > MAX_QUANTITY // ratio:
                raise InventoryError("换算后的入库数量过大。")
            InboundLine.objects.create(document=document, sku=sku, unit_version=version,
                                       quantity=quantity, operation_unit=(version.sale_unit if unit == "SALE"
                                                                            else version.base_unit),
                                       ratio=ratio, base_quantity=quantity * ratio,
                                       base_unit=version.base_unit)
        audit(request, "inventory.inbound.draft", "inbound", document.id, actor,
              after={"warehouseId": str(warehouse.id), "lineCount": len(planned)})
    return inbound_data(document)


def confirm_inbound(request, actor, document_id, expected_revision, key):
    with transaction.atomic():
        document = (InboundDocument.objects.select_for_update(of=("self",))
                    .select_related("warehouse", "created_by").filter(id=document_id).first())
        if not document:
            raise InventoryError("入库单不存在。", "NOT_FOUND", 404)
        if document.status == InboundDocument.Status.CONFIRMED:
            if document.confirmation_key == key and document.confirmed_by_id == actor.id:
                return inbound_data(document)
            raise InventoryError("入库单已确认。", "ALREADY_CONFIRMED", 409)
        if document.revision != expected_revision:
            raise InventoryError("入库单已被修改。", "REVISION_CONFLICT", 409)
        if not document.warehouse.enabled:
            raise InventoryError("仓库已停用。", "WAREHOUSE_UNAVAILABLE", 409)
        if InboundDocument.objects.filter(confirmation_key=key).exists():
            raise InventoryError("请求标识已被其他入库单使用。", "IDEMPOTENCY_CONFLICT", 409)
        lines = list(document.lines.select_related("sku", "unit_version").order_by("sku_id"))
        skus = {sku.id: sku for sku in lock_stock_skus([line.sku_id for line in lines])}
        if any(skus[line.sku_id].current_unit_id != line.unit_version_id for line in lines):
            raise InventoryError("SKU 单位换算已变化，请重新创建入库单。", "UNIT_VERSION_CHANGED", 409)
        anchors = resolve_anchor_ids([line.sku_id for line in lines])
        for anchor in sorted(set(anchors.values())):
            if anchor in skus:
                ensure_independent_pool(skus[anchor])
        for anchor in sorted(set(anchors.values())):
            InventoryBalance.objects.get_or_create(
                warehouse=document.warehouse, sku_id=anchor,
                defaults={"on_hand_base_units": 0, "reserved_base_units": 0})
        locked = {balance.sku_id: balance for balance in InventoryBalance.objects.select_for_update().filter(
            warehouse=document.warehouse, sku_id__in=set(anchors.values())).order_by("sku_id")}
        for line in lines:
            balance = locked[anchors[line.sku_id]]
            before = balance.on_hand_base_units
            if before > MAX_QUANTITY - line.base_quantity:
                raise InventoryError("入库后库存数量过大。")
            balance.on_hand_base_units = before + line.base_quantity
            balance.save(update_fields=["on_hand_base_units", "updated_at"])
            InventoryLedger.objects.create(
                warehouse=document.warehouse, sku_id=line.sku_id, unit_version=line.unit_version,
                inbound_line=line, operation_unit=line.operation_unit,
                operation_quantity=line.quantity, ratio=line.ratio,
                delta_base_units=line.base_quantity, balance_before=before,
                balance_after=balance.on_hand_base_units, reason=document.reason, actor=actor)
        document.status = InboundDocument.Status.CONFIRMED
        document.revision += 1
        document.confirmation_key = key
        document.confirmed_by = actor
        document.confirmed_at = timezone.now()
        document.save(update_fields=["status", "revision", "confirmation_key", "confirmed_by", "confirmed_at"])
        audit(request, "inventory.inbound.confirm", "inbound", document.id, actor,
              after={"warehouseId": str(document.warehouse_id), "lineCount": len(lines)})
    return inbound_data(document)
