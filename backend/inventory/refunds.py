"""Inventory-owned refund restitution using original warehouse and unit snapshots."""
import uuid
from django.db import transaction
from django.db.models import Sum
from .models import InventoryBalance, InventoryLedger, InventoryReservation
from .pool_access import resolve_anchor_id


def restore_unshipped_refund_locked(line, quantity, case_id):
    """Caller holds Order and applies trusted money plus case state atomically."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("退款回库必须在订单事务内。")
    if type(quantity) is not int or not 1 <= quantity <= line.quantity or not isinstance(case_id, uuid.UUID):
        raise ValueError("退款回库数量或来源不正确。")
    if line.fulfillment_kind != "SHIP":
        raise ValueError("此入口只回补未发货实物。")
    from fulfillment.store_service import physical_handoff_fact
    if physical_handoff_fact(line.order) is not None:
        raise ValueError("已经交付的实物须通过退货验收回库。")
    previous = InventoryLedger.objects.filter(refund_case_id=case_id).first()
    if previous:
        if previous.refund_order_line_id != line.id or previous.operation_quantity != quantity:
            raise ValueError("退款回库请求身份冲突。")
        return previous
    reservation = InventoryReservation.objects.filter(order_line=line, status="CONSUMED").first()
    sale = InventoryLedger.objects.filter(order_line=line, movement_type="SALE").first()
    if (reservation is None or sale is None or reservation.base_quantity != line.base_quantity or
            sale.warehouse_id != line.warehouse_id or sale.sku_id != line.sku_id or
            sale.unit_version_id != line.unit_version_id or sale.delta_base_units != -line.base_quantity):
        raise ValueError("原销售库存凭证不一致。")
    prior_quantity = InventoryLedger.objects.filter(refund_order_line=line).aggregate(
        total=Sum("operation_quantity"))["total"] or 0
    if prior_quantity + quantity > line.quantity:
        raise ValueError("累计退款回库超过原购买数量。")
    balance = InventoryBalance.objects.select_for_update().get(pk=reservation.balance_id)
    if balance.warehouse_id != line.warehouse_id or balance.sku_id != resolve_anchor_id(line.sku_id):
        raise ValueError("原销售余额与订单项不一致。")
    before = balance.on_hand_base_units
    balance.on_hand_base_units += quantity * line.ratio
    balance.save(update_fields=["on_hand_base_units", "updated_at"])
    return InventoryLedger.objects.create(warehouse_id=line.warehouse_id, sku_id=line.sku_id,
        unit_version_id=line.unit_version_id, refund_order_line=line, refund_case_id=case_id,
        movement_type="REFUND", operation_unit=line.sale_unit, operation_quantity=quantity,
        ratio=line.ratio, delta_base_units=quantity * line.ratio, balance_before=before,
        balance_after=balance.on_hand_base_units, reason="未发货退款成功回库", note=str(case_id), actor=None)


def record_return_disposition_locked(line, acceptance, actor):
    """Record actual returned units; salable stock joins balance before funds refund."""
    from .models import ReturnDisposition
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("退货验收库存必须在订单事务内。")
    previous=ReturnDisposition.objects.filter(case_id=acceptance.case_id).first()
    if previous:
        if previous.acceptance_id!=acceptance.id: raise ValueError("退货库存来源冲突。")
        return previous
    if line.fulfillment_kind!="SHIP": raise ValueError("只接受实物退货。")
    reservation=InventoryReservation.objects.filter(order_line=line,status="CONSUMED").first()
    sale=InventoryLedger.objects.filter(order_line=line,movement_type="SALE").first()
    if (not reservation or not sale or reservation.base_quantity!=line.base_quantity or
        sale.delta_base_units!=-line.base_quantity or sale.warehouse_id!=line.warehouse_id or
        sale.sku_id!=line.sku_id or sale.unit_version_id!=line.unit_version_id or sale.ratio!=line.ratio):
        raise ValueError("原销售库存凭证不一致。")
    prior=ReturnDisposition.objects.filter(order_line=line).aggregate(total=Sum("received_quantity"))["total"] or 0
    if prior+acceptance.received_quantity>line.quantity: raise ValueError("累计实际退回数量超过购买数量。")
    result=ReturnDisposition.objects.create(case_id=acceptance.case_id,acceptance_id=acceptance.id,order_line=line,
        received_quantity=acceptance.received_quantity,salable_quantity=acceptance.salable_quantity,
        damaged_quantity=acceptance.received_quantity-acceptance.salable_quantity,actor=actor,reason=acceptance.reason)
    if acceptance.salable_quantity:
        balance=InventoryBalance.objects.select_for_update().get(pk=reservation.balance_id)
        if balance.warehouse_id!=line.warehouse_id or balance.sku_id!=resolve_anchor_id(line.sku_id):
            raise ValueError("原销售余额与订单项不一致。")
        before=balance.on_hand_base_units
        balance.on_hand_base_units+=acceptance.salable_quantity*line.ratio
        balance.save(update_fields=["on_hand_base_units","updated_at"])
        InventoryLedger.objects.create(warehouse_id=line.warehouse_id,sku_id=line.sku_id,unit_version_id=line.unit_version_id,
            return_order_line=line,return_case_id=acceptance.case_id,movement_type="RETURN",operation_unit=line.sale_unit,
            operation_quantity=acceptance.salable_quantity,ratio=line.ratio,delta_base_units=acceptance.salable_quantity*line.ratio,
            balance_before=before,balance_after=balance.on_hand_base_units,reason="已发货退货验收可售回库",note=str(acceptance.id),actor=actor)
    return result
