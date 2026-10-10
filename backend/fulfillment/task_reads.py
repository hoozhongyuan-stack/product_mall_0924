"""Overlapping order tasks, shared by paginated lists and member counters.

These predicates describe currently actionable fulfillment, not the single
fulfillmentStatus display label. Mixed orders may belong to several tasks.
"""
from django.db.models import BooleanField, Case, Count, Exists, F, OuterRef, Q, When
from django.utils import timezone

from aftersales.task_reads import active_cases, with_line_quantities
from orders.models import OrderLine
from .models import Shipment, StoreDelivery


TASK_FIELDS = {
    'WAITING_SHIPMENT': 'task_waiting_shipment',
    'WAITING_PREPARATION': 'task_waiting_preparation',
    'WAITING_PICKUP': 'task_waiting_pickup',
    'WAITING_DELIVERY': 'task_waiting_delivery',
    'DELIVERING': 'task_delivering',
    'IN_TRANSIT': 'task_in_transit',
    'WAITING_REDEMPTION': 'task_waiting_redemption',
    'AFTER_SALE': 'task_after_sale',
}


def with_order_tasks(orders):
    lines = with_line_quantities(OrderLine.objects.filter(order_id=OuterRef('pk')))
    shipping = lines.filter(fulfillment_kind='SHIP', quantity__gt=F('task_refunded_quantity'))
    shipments = Shipment.objects.filter(order_id=OuterRef('pk'))
    cases = active_cases().filter(order_line__order_id=OuterRef('pk'))
    redeemable = lines.filter(
        fulfillment_kind='REDEEM', redeem_voucher__valid_until__gte=timezone.localdate(),
        quantity__gt=F('redeem_voucher__redeemed_quantity') + F('redeem_voucher__voided_quantity')
        + F('task_held_unredeemed'),
    )
    paid = Q(status='PAID')
    local = StoreDelivery.objects.filter(order_id=OuterRef('pk'))
    actionable = paid & Exists(shipping) & ~Exists(cases.filter(order_line__fulfillment_kind='SHIP'))
    predicates = {
        'task_waiting_shipment': paid & ~Q(delivery_mode__in=['PICKUP','DELIVERY']) & Exists(shipping) & ~Exists(shipments)
        & ~Exists(cases.filter(order_line__fulfillment_kind='SHIP')),
        'task_waiting_preparation': actionable & Q(delivery_mode__in=['PICKUP','DELIVERY']) & ~Exists(local),
        'task_waiting_pickup': actionable & Q(delivery_mode='PICKUP') & Exists(local.filter(status='READY')),
        'task_waiting_delivery': actionable & Q(delivery_mode='DELIVERY') & Exists(local.filter(status='READY')),
        'task_delivering': actionable & Q(delivery_mode='DELIVERY') & Exists(local.filter(status='IN_TRANSIT')),
        'task_in_transit': paid & Exists(shipping) & Exists(shipments.filter(confirmed_at__isnull=True)),
        'task_waiting_redemption': paid & Exists(redeemable),
        'task_after_sale': Exists(cases),
    }
    return orders.alias(**{
        name: Case(When(condition, then=True), default=False, output_field=BooleanField())
        for name, condition in predicates.items()
    })


def filter_order_tasks(orders, task):
    """The boundary validates the public enum; SQL filters before count/slicing."""
    return with_order_tasks(orders).filter(**{TASK_FIELDS[task]: True})


def order_task_counts(orders):
    """One aggregate statement, irrespective of the member's order count."""
    return with_order_tasks(orders).aggregate(
        pendingPayment=Count('pk', filter=Q(status='PENDING_PAYMENT')),
        waitingShipment=Count('pk', filter=Q(task_waiting_shipment=True)),
        inTransit=Count('pk', filter=Q(task_in_transit=True)),
        waitingPreparation=Count('pk', filter=Q(task_waiting_preparation=True)),
        waitingPickup=Count('pk', filter=Q(task_waiting_pickup=True)),
        waitingDelivery=Count('pk', filter=Q(task_waiting_delivery=True)),
        delivering=Count('pk', filter=Q(task_delivering=True)),
        waitingRedemption=Count('pk', filter=Q(task_waiting_redemption=True)),
        afterSale=Count('pk', filter=Q(task_after_sale=True)),
    )
