"""Inventory-owned order holds. Call only inside the order transaction."""

from django.db import transaction
from django.utils import timezone

from .models import InventoryBalance, InventoryLedger, InventoryReservation, InventoryReservationEvent


class ReservationError(ValueError):
    def __init__(self, message, code="OUT_OF_STOCK"):
        super().__init__(message)
        self.code = code


def lock_default_balances(warehouse_id, sku_ids):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("锁定库存余额必须在数据库事务内执行。")
    rows = InventoryBalance.objects.select_for_update().filter(
        warehouse_id=warehouse_id, sku_id__in=sku_ids).order_by("warehouse_id", "sku_id")
    return {row.sku_id: row for row in rows}


def reserve_order_lines(lines, locked_balances):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("订单占库必须在同一数据库事务内执行。")
    lines = list(lines)
    if not lines:
        raise ReservationError("订单没有商品行。", "VALIDATION_FAILED")
    warehouse_ids = {line.warehouse_id for line in lines}
    if len(warehouse_ids) != 1:
        raise ReservationError("订单须使用同一默认仓。", "WAREHOUSE_UNAVAILABLE")
    requested = {line.sku_id: line for line in lines}
    if len(requested) != len(lines):
        raise ReservationError("订单内 SKU 不可重复。", "VALIDATION_FAILED")
    if len(locked_balances) != len(requested):
        raise ReservationError("商品库存不足，请重新确认订单。")
    for sku_id in sorted(requested):
        line = requested[sku_id]
        balance = locked_balances[sku_id]
        if balance.on_hand_base_units - balance.reserved_base_units < line.base_quantity:
            raise ReservationError("商品库存不足，请重新确认订单。")
        balance.reserved_base_units += line.base_quantity
        balance.save(update_fields=["reserved_base_units", "updated_at"])
        reservation = InventoryReservation.objects.create(
            order_line=line, balance=balance, base_quantity=line.base_quantity)
        InventoryReservationEvent.objects.create(
            reservation=reservation, action=InventoryReservationEvent.Action.RESERVE,
            base_quantity=line.base_quantity)


def release_order_reservations(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("释放占库必须在同一数据库事务内执行。")
    reservations = list(InventoryReservation.objects.filter(order_line__order=order,
                                                            status=InventoryReservation.Status.ACTIVE)
                        .select_related("balance").order_by("balance__warehouse_id", "balance__sku_id"))
    balance_ids = [reservation.balance_id for reservation in reservations]
    balances = {balance.id: balance for balance in InventoryBalance.objects.select_for_update().filter(
        id__in=balance_ids).order_by("warehouse_id", "sku_id")}
    for reservation in reservations:
        balance = balances[reservation.balance_id]
        if balance.reserved_base_units < reservation.base_quantity:
            raise RuntimeError("库存锁定量与订单占用不一致。")
        balance.reserved_base_units -= reservation.base_quantity
        balance.save(update_fields=["reserved_base_units", "updated_at"])
        reservation.status = InventoryReservation.Status.RELEASED
        reservation.released_at = timezone.now()
        reservation.save(update_fields=["status", "released_at"])
        InventoryReservationEvent.objects.create(
            reservation=reservation, action=InventoryReservationEvent.Action.RELEASE,
            base_quantity=reservation.base_quantity)


def consume_order_reservations(order):
    """Convert held stock to sold stock and append a SALE ledger movement."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("确认订单库存必须在同一数据库事务内执行。")
    reservations = list(InventoryReservation.objects.filter(
        order_line__order=order, status=InventoryReservation.Status.ACTIVE)
        .select_related("order_line").order_by("balance__warehouse_id", "balance__sku_id"))
    if not reservations:
        return False
    balance_ids = [reservation.balance_id for reservation in reservations]
    balances = {balance.id: balance for balance in InventoryBalance.objects.select_for_update().filter(
        id__in=balance_ids).order_by("warehouse_id", "sku_id")}
    for reservation in reservations:
        balance = balances[reservation.balance_id]
        quantity = reservation.base_quantity
        if balance.reserved_base_units < quantity or balance.on_hand_base_units < quantity:
            raise RuntimeError("库存锁定量与订单占用不一致。")
        before = balance.on_hand_base_units
        balance.on_hand_base_units -= quantity
        balance.reserved_base_units -= quantity
        balance.save(update_fields=["on_hand_base_units", "reserved_base_units", "updated_at"])
        reservation.status = InventoryReservation.Status.CONSUMED
        reservation.consumed_at = timezone.now()
        reservation.save(update_fields=["status", "consumed_at"])
        InventoryReservationEvent.objects.create(
            reservation=reservation, action=InventoryReservationEvent.Action.CONSUME,
            base_quantity=quantity)
        line = reservation.order_line
        InventoryLedger.objects.create(
            warehouse_id=balance.warehouse_id, sku_id=balance.sku_id,
            unit_version_id=line.unit_version_id, order_line=line,
            movement_type=InventoryLedger.MovementType.SALE,
            operation_unit=line.sale_unit, operation_quantity=line.quantity,
            ratio=line.ratio, delta_base_units=-quantity,
            balance_before=before, balance_after=balance.on_hand_base_units,
            reason="订单收款确认", note=order.order_no, actor=None)
    return True


def assert_order_reservations(order):
    """Ensure every order item still owns its original active stock hold."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("核对订单占用必须在同一数据库事务内执行。")
    lines = {line.id: (line.base_quantity, line.warehouse_id, line.sku_id) for line in order.lines.all()}
    reservations = list(InventoryReservation.objects.filter(order_line__order=order)
                        .values_list("order_line_id", "base_quantity", "balance__warehouse_id",
                                     "balance__sku_id", "status"))
    if (not lines or len(reservations) != len(lines) or
            any(line_id not in lines or (quantity, warehouse_id, sku_id) != lines[line_id] or
                status != InventoryReservation.Status.ACTIVE
                for line_id, quantity, warehouse_id, sku_id, status in reservations)):
        raise RuntimeError("订单库存占用与成交快照不一致。")
