"""Preview and atomically replace a draft product's specification matrix."""

import hashlib
import json
import uuid

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.db.models.functions import Upper

from accounts.security import audit
from inventory.pool_access import (PoolBindingError, pool_change_allowed,
                                   remove_unused_pool_membership, update_empty_pool_base_unit)
from inventory.sku_guards import referenced_sku_ids

from .models import (MemberGrade, Product, Sku, SkuGradePrice, SkuSpecSelection,
                     SkuUnitVersion, SpecAxis, SpecOption)
from .presentation import product_data
from .service import parse_prices, parse_unit
from .validation import CatalogError, code_field, list_field, number_field, object_field, text_field, uuid_field


TOKEN_SALT = "catalog.product-spec-edit"


def payload_digest(values):
    data = {key: value for key, value in values.items() if key != "previewToken"}
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def sku_snapshot(skus):
    return [[str(sku.id), sku.revision, sku.sku_code] for sku in sorted(skus, key=lambda row: str(row.id))]


def reject_inventory_rewrites(plan):
    protected_ids = referenced_sku_ids(
        [sku.id for sku in plan["removed"]] +
        [row["matched"].id for row in plan["skus"] if row["matched"]]
    )
    for sku in plan["removed"]:
        if sku.id in protected_ids:
            raise CatalogError(f"SKU {sku.sku_code} 已有关联库存单据或余额，不能移除规格组合。",
                               "SKU_INVENTORY_REFERENCED", 409)
        if not pool_change_allowed(sku.id, removing=True):
            raise CatalogError(f"SKU {sku.sku_code} 已有共享库存池或历史记录，不能移除规格组合。",
                               "SKU_INVENTORY_REFERENCED", 409)
    for row in plan["skus"]:
        sku = row["matched"]
        if (sku and sku.id not in protected_ids and sku.current_unit and
                sku.current_unit.base_unit != row["unit"]["base_unit"] and
                not pool_change_allowed(sku.id, new_base_unit=row["unit"]["base_unit"])):
            raise CatalogError(f"SKU {sku.sku_code} 的共享库存池不能修改基础单位。",
                               "POOL_UNIT_MISMATCH", 409)
    if not protected_ids:
        return

    for row in plan["skus"]:
        sku = row["matched"]
        if not sku or sku.id not in protected_ids:
            continue
        if sku.sku_code != row["code"]:
            raise CatalogError(f"SKU {sku.sku_code} 已有关联库存单据或余额，不能修改编码。",
                               "SKU_INVENTORY_REFERENCED", 409)
        current_unit = sku.current_unit
        if not current_unit or current_unit.base_unit != row["unit"]["base_unit"]:
            raise CatalogError(f"SKU {sku.sku_code} 已有关联库存单据或余额，不能修改基础单位。",
                               "SKU_INVENTORY_REFERENCED", 409)

    protected_codes = {
        row["matched"].id: row["matched"].sku_code
        for row in plan["skus"] if row["matched"] and row["matched"].id in protected_ids
    }
    selections = list(SkuSpecSelection.objects.filter(sku_id__in=protected_ids)
                      .values_list("sku_id", "axis_id", "option_id"))
    protected_axes = {axis_id: protected_codes[sku_id] for sku_id, axis_id, _ in selections}
    protected_options = {option_id: protected_codes[sku_id] for sku_id, _, option_id in selections}
    for axis in plan["axes"]:
        previous_axis = plan["old_axes"].get(axis["id"])
        if (axis["id"] in protected_axes and previous_axis and
                previous_axis.name != axis["name"]):
            raise CatalogError(f"SKU {protected_axes[axis['id']]} 已有关联库存单据或余额，不能修改规格项名称。",
                               "SKU_INVENTORY_REFERENCED", 409)
        for option in axis["options"]:
            previous_option = plan["old_options"].get(option["id"])
            if (option["id"] in protected_options and previous_option and
                    previous_option.value != option["value"]):
                raise CatalogError(f"SKU {protected_options[option['id']]} 已有关联库存单据或余额，不能修改规格值。",
                                   "SKU_INVENTORY_REFERENCED", 409)


def parse_axes(product, raw_axes):
    existing = {axis.id: axis for axis in SpecAxis.objects.filter(product=product)}
    existing_options = {option.id: option for option in SpecOption.objects.filter(axis_id__in=existing)}
    axes, option_by_key = [], {}
    seen_axis_ids, seen_option_ids, axis_keys, axis_names, axis_orders = set(), set(), set(), set(), set()
    for raw in list_field(raw_axes, "规格项", 2):
        raw = object_field(raw, "规格项")
        if set(raw) - {"id", "clientKey", "name", "sortOrder", "options"}:
            raise CatalogError("规格项含有不支持的字段。")
        key = text_field(raw.get("clientKey"), "规格项标识", 80)
        name = text_field(raw.get("name"), "规格项名称", 60)
        order = number_field(raw.get("sortOrder"), "规格项顺序", 0, 100)
        if key in axis_keys or name.casefold() in axis_names or order in axis_orders:
            raise CatalogError("规格项标识、名称及顺序不能重复。")
        axis_keys.add(key); axis_names.add(name.casefold()); axis_orders.add(order)
        axis_id = uuid_field(raw["id"], "规格项 ID") if "id" in raw else uuid.uuid5(product.id, f"axis:{key}")
        if axis_id in seen_axis_ids or ("id" in raw and axis_id not in existing):
            raise CatalogError("规格项 ID 不属于此商品或重复。")
        if "id" not in raw and axis_id in existing:
            raise CatalogError("新规格项标识与已有规格项冲突。")
        seen_axis_ids.add(axis_id)
        options, option_values, option_orders = [], set(), set()
        for option_raw in list_field(raw.get("options"), "规格值", 20, 1):
            option_raw = object_field(option_raw, "规格值")
            if set(option_raw) - {"id", "clientKey", "value", "sortOrder"}:
                raise CatalogError("规格值含有不支持的字段。")
            option_key = text_field(option_raw.get("clientKey"), "规格值标识", 80)
            value = text_field(option_raw.get("value"), "规格值", 60)
            option_order = number_field(option_raw.get("sortOrder"), "规格值顺序", 0, 100)
            if option_key in option_by_key or value.casefold() in option_values or option_order in option_orders:
                raise CatalogError("同一规格项的值、顺序及规格值标识不能重复。")
            option_values.add(value.casefold()); option_orders.add(option_order)
            option_id = (uuid_field(option_raw["id"], "规格值 ID") if "id" in option_raw else
                         uuid.uuid5(product.id, f"option:{option_key}"))
            old = existing_options.get(option_id)
            if option_id in seen_option_ids or ("id" in option_raw and (not old or old.axis_id != axis_id)):
                raise CatalogError("规格值 ID 不属于此规格项或重复。")
            if "id" not in option_raw and old:
                raise CatalogError("新规格值标识与已有规格值冲突。")
            seen_option_ids.add(option_id)
            options.append({"id": option_id, "axis_id": axis_id, "value": value, "order": option_order})
            option_by_key[option_key] = (axis_id, option_id)
        axes.append({"id": axis_id, "name": name, "order": order, "options": options})
    return axes, option_by_key, existing, existing_options


def parse_plan(product, values, current_skus):
    if set(values) - {"expectedRevision", "specAxes", "skus", "previewToken"}:
        raise CatalogError("规格编辑请求含有不支持的字段。")
    revision = number_field(values.get("expectedRevision"), "预期修订号", 1)
    if product.revision != revision:
        raise CatalogError("商品已被其他人修改。", "REVISION_CONFLICT", 409)
    if product.status != Product.Status.DRAFT:
        raise CatalogError("仅草稿商品可编辑规格与 SKU。", "PRODUCT_NOT_DRAFT", 409)
    axes, option_by_key, old_axes, old_options = parse_axes(product, values.get("specAxes"))
    current_by_key = {sku.spec_key: sku for sku in current_skus}
    enabled_grades = set(MemberGrade.objects.filter(enabled=True).values_list("id", flat=True))
    planned, used_codes, used_keys, matched_ids = [], set(), set(), set()
    axis_ids = {axis["id"] for axis in axes}
    for raw in list_field(values.get("skus"), "SKU", 100, 1):
        raw = object_field(raw, "SKU")
        if set(raw) - {"id", "expectedSkuRevision", "skuCode", "specOptionKeys", "listPriceFen",
                        "saleStatus", "gradePrices", "unit"}:
            raise CatalogError("SKU 含有不支持的字段。")
        code = code_field(raw.get("skuCode"), "SKU 编码")
        keys = list_field(raw.get("specOptionKeys"), "SKU 规格选择", 2)
        if any(not isinstance(key, str) or key not in option_by_key for key in keys):
            raise CatalogError("SKU 规格值不属于此商品。")
        selected = [option_by_key[key] for key in keys]
        if len(keys) != len(axes) or {axis_id for axis_id, _ in selected} != axis_ids:
            raise CatalogError("每个 SKU 必须从每项规格各选一个值。")
        spec_key = ":".join(sorted(str(option_id) for _, option_id in selected)) if axes else "single"
        if spec_key in used_keys or code.casefold() in used_codes:
            raise CatalogError("SKU 编码或规格组合重复。")
        used_keys.add(spec_key); used_codes.add(code.casefold())
        matched = current_by_key.get(spec_key)
        if matched:
            if "id" not in raw or uuid_field(raw["id"], "SKU ID") != matched.id:
                raise CatalogError("已有规格组合须沿用原 SKU ID。", "SKU_SPEC_IMMUTABLE", 409)
            if number_field(raw.get("expectedSkuRevision"), "SKU 预期修订号", 1) != matched.revision:
                raise CatalogError("SKU 已被其他人修改。", "REVISION_CONFLICT", 409)
            matched_ids.add(matched.id)
        elif "id" in raw or "expectedSkuRevision" in raw:
            raise CatalogError("SKU ID 与规格组合不匹配。", "SKU_SPEC_IMMUTABLE", 409)
        status = raw.get("saleStatus")
        if status not in Sku.SaleStatus.values:
            raise CatalogError("SKU 销售状态不正确。")
        if status == Sku.SaleStatus.ON_SALE:
            raise CatalogError("草稿商品的 SKU 须先保存，再从 SKU 列表上架。")
        planned.append({"matched": matched, "code": code, "spec_key": spec_key,
                        "selection": selected,
                        "price": number_field(raw.get("listPriceFen"), "日常价", 0, 2**53 - 1),
                        "status": status,
                        "grade_prices": parse_prices(raw.get("gradePrices", []), enabled_grades),
                        "unit": parse_unit(raw.get("unit"))})
    removed = [sku for sku in current_skus if sku.id not in matched_ids]
    # A code may be reused from a SKU explicitly removed in this same edit.
    collisions = (Sku.objects.annotate(normalized_code=Upper("sku_code"))
                  .filter(normalized_code__in=[row["code"].upper() for row in planned])
                  .exclude(id__in=[sku.id for sku in current_skus]))
    if collisions.exists():
        raise CatalogError("SKU 编码已存在。", "SKU_CODE_DUPLICATE", 409)
    plan = {"axes": axes, "options": option_by_key, "old_axes": old_axes,
            "old_options": old_options, "skus": planned, "removed": removed,
            "snapshot": sku_snapshot(current_skus)}
    reject_inventory_rewrites(plan)
    return plan


def preview_data(plan):
    removed_ids = [sku.id for sku in plan["removed"]]
    prices = {}
    units = {}
    if removed_ids:
        from django.db.models import Count
        prices = dict(SkuGradePrice.objects.filter(sku_id__in=removed_ids).values("sku_id")
                      .annotate(total=Count("id")).values_list("sku_id", "total"))
        units = dict(SkuUnitVersion.objects.filter(sku_id__in=removed_ids).values("sku_id")
                     .annotate(total=Count("id")).values_list("sku_id", "total"))
    return {
        "retained": [{"skuId": str(row["matched"].id), "skuCode": row["code"]}
                     for row in plan["skus"] if row["matched"]],
        "added": [{"skuCode": row["code"]} for row in plan["skus"] if not row["matched"]],
        "removed": [{"skuId": str(sku.id), "skuCode": sku.sku_code,
                     "gradePriceCount": prices.get(sku.id, 0), "unitVersionCount": units.get(sku.id, 0)}
                    for sku in plan["removed"]],
    }


def preview_specs(actor, product_id, values):
    product = Product.objects.filter(id=product_id).first()
    if not product:
        raise CatalogError("商品不存在。", "NOT_FOUND", 404)
    current = list(Sku.objects.select_related("current_unit").filter(product=product).order_by("id"))
    plan = parse_plan(product, values, current)
    result = preview_data(plan)
    token = signing.dumps({"actorId": str(actor.id), "productId": str(product.id),
                           "digest": payload_digest(values), "snapshot": plan["snapshot"],
                           "removed": result["removed"]}, salt=TOKEN_SALT, compress=True)
    return {"previewToken": token, **result}


def validate_token(actor, product, values, plan):
    token = values.get("previewToken")
    if not isinstance(token, str) or not 1 <= len(token) <= 16384:
        raise CatalogError("请先预览规格变更影响。", "PREVIEW_REQUIRED", 409)
    try:
        signed = signing.loads(token, salt=TOKEN_SALT, max_age=600)
    except signing.BadSignature as exc:
        raise CatalogError("预览已过期或无效，请重新预览。", "PREVIEW_STALE", 409) from exc
    if (signed.get("actorId") != str(actor.id) or signed.get("productId") != str(product.id) or
            signed.get("digest") != payload_digest(values) or signed.get("snapshot") != plan["snapshot"] or
            signed.get("removed") != preview_data(plan)["removed"]):
        raise CatalogError("商品或 SKU 已变化，请重新预览。", "PREVIEW_STALE", 409)


def replace_draft_data(product, plan):
    # Current draft-only removals have no transaction history yet. Preserve an
    # impact snapshot in audit, then delete all dependent current records.
    for sku in plan["removed"]:
        remove_unused_pool_membership(sku.id)
        SkuGradePrice.objects.filter(sku=sku).delete()
        SkuSpecSelection.objects.filter(sku=sku).delete()
        if sku.current_unit_id:
            sku.current_unit = None
            sku.save(update_fields=["current_unit", "updated_at"])
        SkuUnitVersion.objects.filter(sku=sku).delete()
        sku.delete()  # Future order/inventory PROTECT references block removal.

    # Release old globally unique codes before assigning the proposed matrix.
    # This also permits two retained SKUs to exchange codes in one transaction.
    for row in plan["skus"]:
        sku = row["matched"]
        if sku and sku.sku_code != row["code"]:
            Sku.objects.filter(pk=sku.pk).update(sku_code=f"TMP-{uuid.uuid4().hex}")

    desired_axis_ids = {row["id"] for row in plan["axes"]}
    desired_option_ids = {option["id"] for axis in plan["axes"] for option in axis["options"]}
    # Move existing positions out of the allowed input range before reordering.
    for index, axis in enumerate(plan["old_axes"].values()):
        axis.sort_order = 1000 + index
        axis.save(update_fields=["sort_order"])
    for index, option in enumerate(plan["old_options"].values()):
        option.sort_order = 1000 + index
        option.save(update_fields=["sort_order"])
    SpecOption.objects.filter(axis__product=product).exclude(id__in=desired_option_ids).delete()
    SpecAxis.objects.filter(product=product).exclude(id__in=desired_axis_ids).delete()
    for row in plan["axes"]:
        axis = plan["old_axes"].get(row["id"])
        if axis:
            axis.name, axis.sort_order = row["name"], row["order"]
            axis.save(update_fields=["name", "sort_order"])
        else:
            axis = SpecAxis.objects.create(id=row["id"], product=product,
                                           name=row["name"], sort_order=row["order"])
        for option_row in row["options"]:
            option = plan["old_options"].get(option_row["id"])
            if option:
                option.value, option.sort_order = option_row["value"], option_row["order"]
                option.save(update_fields=["value", "sort_order"])
            else:
                SpecOption.objects.create(id=option_row["id"], axis=axis,
                                          value=option_row["value"], sort_order=option_row["order"])

    for row in plan["skus"]:
        sku = row["matched"]
        if not sku:
            sku = Sku.objects.create(product=product, sku_code=row["code"], spec_key=row["spec_key"],
                                     list_price_fen=row["price"], sale_status=row["status"])
            SkuSpecSelection.objects.bulk_create([
                SkuSpecSelection(sku=sku, axis_id=axis_id, option_id=option_id)
                for axis_id, option_id in row["selection"]])
            version = SkuUnitVersion.objects.create(sku=sku, **row["unit"])
            sku.current_unit = version
            sku.save(update_fields=["current_unit", "updated_at"])
            SkuGradePrice.objects.bulk_create([
                SkuGradePrice(sku=sku, grade_id=grade_id, price_fen=price)
                for grade_id, price in row["grade_prices"]])
            continue
        changed = (sku.sku_code != row["code"] or sku.list_price_fen != row["price"] or
                   sku.sale_status != row["status"])
        sku.sku_code, sku.list_price_fen, sku.sale_status = row["code"], row["price"], row["status"]
        existing_prices = dict(SkuGradePrice.objects.filter(sku=sku, active=True, grade__enabled=True)
                               .values_list("grade_id", "price_fen"))
        if existing_prices != dict(row["grade_prices"]):
            SkuGradePrice.objects.filter(sku=sku, active=True, grade__enabled=True).update(active=False)
            SkuGradePrice.objects.bulk_create([
                SkuGradePrice(sku=sku, grade_id=grade_id, price_fen=price)
                for grade_id, price in row["grade_prices"]])
            changed = True
        current = sku.current_unit
        if not current or (current.base_unit, current.sale_unit, current.ratio) != (
                row["unit"]["base_unit"], row["unit"]["sale_unit"], row["unit"]["ratio"]):
            update_empty_pool_base_unit(sku.id, row["unit"]["base_unit"])
            sku.current_unit = SkuUnitVersion.objects.create(sku=sku, **row["unit"])
            changed = True
        if changed:
            sku.revision += 1
            sku.save(update_fields=["sku_code", "list_price_fen", "sale_status",
                                    "current_unit", "revision", "updated_at"])
    product.revision += 1
    product.save(update_fields=["revision", "updated_at"])


def save_specs(request, actor, product_id, values):
    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().filter(id=product_id).first()
            if not product:
                raise CatalogError("商品不存在。", "NOT_FOUND", 404)
            current = list(Sku.objects.select_for_update(of=("self",)).select_related("current_unit")
                           .filter(product=product).order_by("id"))
            plan = parse_plan(product, values, current)
            validate_token(actor, product, values, plan)
            impact = preview_data(plan)
            replace_draft_data(product, plan)
            audit(request, "product.specs.update", "product", product.id, actor,
                  before={"skuIds": [str(sku.id) for sku in current]}, after=impact)
        return product_data(product)
    except (IntegrityError, ProtectedError, PoolBindingError) as exc:
        raise CatalogError("规格或 SKU 与当前数据冲突，请刷新后重试。", "CATALOG_CONFLICT", 409) from exc
