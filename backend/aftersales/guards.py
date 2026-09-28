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
    ids = [line.id for line in lines if line.fulfillment_kind == "SHIP"]
    if AfterSaleCase.objects.filter(order_line_id__in=ids, status__in=ACTIVE).exists():
        raise AfterSaleError("订单发货项仍有待处理售后，暂不可发货。", "AFTERSALE_BLOCKS_SHIPMENT", 409)
    from .read import line_summaries
    facts = line_summaries(ids)
    remaining = [line for line in lines if line.fulfillment_kind == "SHIP"
                 and line.quantity > facts.get(line.id, {}).get("refundedQuantity", 0)]
    if ids and not remaining:
        raise AfterSaleError("发货商品已全部退款，没有待发货数量。", "NO_SHIPPING_LINES", 409)
    return remaining


def reserved_unredeemed_quantity(line_id):
    return AfterSaleCase.objects.filter(order_line_id=line_id, status__in=ACTIVE).aggregate(
        quantity=Sum("unredeemed_quantity"))["quantity"] or 0


def assert_reversal_allowed_locked(line):
    _atomic()
    if AfterSaleCase.objects.filter(order_line=line, used_quantity__gt=0, status__in=(*ACTIVE, "COMPLETED")).exists():
        raise AfterSaleError("已核销数量正在售后或已退款，不能撤销核销。", "AFTERSALE_BLOCKS_REVERSAL", 409)
