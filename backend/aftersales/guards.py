"""Fulfillment guards: caller holds the order lock in an atomic transaction."""
from django.db import transaction
from django.db.models import Sum
from .models import AfterSaleCase
from .service import ACTIVE, AfterSaleError


def _atomic():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("After-sale guards require an atomic transaction and order lock")


def assert_shippable_locked(order, lines):
    """Reject active physical holds and return lines with unrefunded units."""
    _atomic()
    from fulfillment.physical import available_physical
    physical = [line for line in lines if line.fulfillment_kind == 'SHIP']
    if not physical:
        return []
    quantities = available_physical(physical)
    remaining = [line for line in physical if quantities[line.id] > 0]
    if not remaining:
        held = AfterSaleCase.objects.filter(order_line_id__in=[line.id for line in physical], status__in=ACTIVE).exists()
        raise AfterSaleError('没有可交付的未冻结商品数量。', 'AFTERSALE_BLOCKS_SHIPMENT' if held else 'NO_SHIPPING_LINES', 409)
    return remaining


def reserved_unredeemed_quantity(line_id):
    return AfterSaleCase.objects.filter(order_line_id=line_id, status__in=ACTIVE).aggregate(
        quantity=Sum("unredeemed_quantity"))["quantity"] or 0


def assert_reversal_allowed_locked(line):
    _atomic()
    if AfterSaleCase.objects.filter(order_line=line, used_quantity__gt=0, status__in=(*ACTIVE, "COMPLETED")).exists():
        raise AfterSaleError("已核销数量正在售后或已退款，不能撤销核销。", "AFTERSALE_BLOCKS_REVERSAL", 409)
