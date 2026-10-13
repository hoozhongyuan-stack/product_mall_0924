"""Quantity-specific physical facts; fulfillment callers hold the order lock."""
from types import SimpleNamespace
from django.db.models import Sum, Q
from .models import LocalHandoffLine, ShipmentLine


def page_physical_quantities(line_ids):
    result = {identity: {'dispatchedQuantity': 0, 'deliveredQuantity': 0} for identity in line_ids}
    if not result:
        return result
    local = LocalHandoffLine.objects.filter(order_line_id__in=line_ids).values('order_line_id').annotate(
        dispatched=Sum('quantity', filter=Q(phase='DISPATCH')),
        delivered=Sum('quantity', filter=Q(phase='COMPLETE')))
    for row in local:
        result[row['order_line_id']] = {'dispatchedQuantity': row['dispatched'] or row['delivered'] or 0,
                                      'deliveredQuantity': row['delivered'] or 0}
    express = ShipmentLine.objects.filter(order_line_id__in=line_ids).values('order_line_id').annotate(
        dispatched=Sum('quantity'), delivered=Sum('quantity', filter=Q(shipment__confirmed_at__isnull=False)))
    for row in express:
        result[row['order_line_id']] = {'dispatchedQuantity': row['dispatched'] or 0,
                                      'deliveredQuantity': row['delivered'] or 0}
    return result


def physical_quantities(line):
    return page_physical_quantities([line.id])[line.id]


def line_handoff_fact(line):
    """Return a date fact only when this order item has physically left its warehouse."""
    local = LocalHandoffLine.objects.filter(order_line=line).select_related('event').order_by('-event__occurred_at').first()
    if local:
        completed = LocalHandoffLine.objects.filter(order_line=line, phase='COMPLETE').select_related('event').order_by('-event__occurred_at').first()
        return SimpleNamespace(confirmed_at=completed.event.occurred_at if completed else None)
    shipped = ShipmentLine.objects.filter(order_line=line).select_related('shipment').order_by('-shipment__shipped_at').first()
    return shipped.shipment if shipped else None


def available_physical(lines, *, facts=None, holds=None):
    from aftersales.read import line_summaries
    facts = facts if facts is not None else page_physical_quantities([line.id for line in lines])
    holds = holds if holds is not None else line_summaries([line.id for line in lines])
    return {line.id: max(0, line.quantity - facts.get(line.id, {}).get('dispatchedQuantity', 0)
                         - holds.get(line.id, {}).get('refundedUnshippedQuantity', 0)
                         - holds.get(line.id, {}).get('reservedUnshippedQuantity', 0)) for line in lines}
