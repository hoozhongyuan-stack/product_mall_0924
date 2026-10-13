"""Overlapping order tasks, shared by paginated lists and member counters.

These predicates describe currently actionable fulfillment, not the single
fulfillmentStatus display label. Mixed orders may belong to several tasks.
"""
from django.db.models import BooleanField, Case, Count, Exists, F, OuterRef, Q, When, IntegerField, Subquery, Sum
from django.utils import timezone

from aftersales.task_reads import active_cases, with_line_quantities
from orders.models import OrderLine
from .models import Shipment, StoreDelivery, ShipmentLine, LocalHandoffLine
from django.db.models.functions import Coalesce, Greatest


TASK_FIELDS = {
    'WAITING_SHIPMENT': 'task_waiting_shipment',
    'WAITING_PREPARATION': 'task_waiting_preparation',
    'WAITING_PICKUP': 'task_waiting_pickup',
    'WAITING_DELIVERY': 'task_waiting_delivery',
    'DELIVERING': 'task_delivering',
    'IN_TRANSIT': 'task_in_transit',
    'WAITING_REDEMPTION': 'task_waiting_redemption',
    'AFTER_SALE': 'task_after_sale',
    'EXCEPTION': 'task_exception',
}


def with_order_tasks(orders, store_id=None):
    lines = with_line_quantities(OrderLine.objects.filter(order_id=OuterRef('pk')))
    shipped = ShipmentLine.objects.filter(order_line_id=OuterRef('pk')).values('order_line_id').annotate(total=Sum('quantity')).values('total')
    local_dispatched = LocalHandoffLine.objects.filter(order_line_id=OuterRef('pk'), phase='DISPATCH').values('order_line_id').annotate(total=Sum('quantity')).values('total')
    local_completed = LocalHandoffLine.objects.filter(order_line_id=OuterRef('pk'), phase='COMPLETE').values('order_line_id').annotate(total=Sum('quantity')).values('total')
    lines = lines.alias(task_dispatched=Coalesce(Subquery(shipped, output_field=IntegerField()), 0) + Greatest(
        Coalesce(Subquery(local_dispatched, output_field=IntegerField()), 0), Coalesce(Subquery(local_completed, output_field=IntegerField()), 0)))
    shipping = lines.filter(fulfillment_kind='SHIP', quantity__gt=F('task_dispatched')+F('task_refunded_unshipped')+F('task_held_unshipped'))
    shipments = Shipment.objects.filter(order_id=OuterRef('pk'))
    cases = active_cases().filter(order_line__order_id=OuterRef('pk'))
    redeemable = lines.filter(
        fulfillment_kind='REDEEM', redeem_voucher__valid_until__gte=timezone.localdate(),
        quantity__gt=F('redeem_voucher__redeemed_quantity') + F('redeem_voucher__voided_quantity')
        + F('task_held_unredeemed'),
    )
    if store_id is not None:
        redeemable = redeemable.filter(redeem_scope_snapshot__contains={'stores': [{'storeId': str(store_id)}]}).filter(redeem_scope_snapshot__scope__in=['STORE', 'BOTH'])
    paid = Q(status='PAID')
    local = StoreDelivery.objects.filter(order_id=OuterRef('pk'))
    actionable = paid & Exists(shipping)
    predicates = {
        'task_waiting_shipment': paid & ~Q(delivery_mode__in=['PICKUP','DELIVERY']) & Exists(shipping),
        'task_waiting_preparation': actionable & Q(delivery_mode__in=['PICKUP','DELIVERY']) & (~Exists(local) | Exists(local.filter(status='COMPLETED'))),
        'task_waiting_pickup': actionable & Q(delivery_mode='PICKUP') & Exists(local.filter(status='READY')),
        'task_waiting_delivery': actionable & Q(delivery_mode='DELIVERY') & Exists(local.filter(status='READY')),
        'task_delivering': paid & Q(delivery_mode='DELIVERY') & Exists(local.filter(status='IN_TRANSIT')),
        'task_in_transit': paid & Exists(lines.filter(fulfillment_kind='SHIP', quantity__gt=F('task_refunded_quantity'))) & (Exists(shipments.filter(confirmed_at__isnull=True)) | (Q(delivery_mode='DELIVERY') & Exists(local.filter(status='IN_TRANSIT')))),
        'task_waiting_redemption': paid & Exists(redeemable),
        'task_after_sale': Exists(cases),
        'task_exception': paid & Q(delivery_mode='DELIVERY') & Exists(local.filter(status='EXCEPTION')),
    }
    return orders.annotate(**{
        name: Case(When(condition, then=True), default=False, output_field=BooleanField())
        for name, condition in predicates.items()
    })


def filter_order_tasks(orders, task, store_id=None):
    """The boundary validates the public enum; SQL filters before count/slicing."""
    return with_order_tasks(orders, store_id=store_id).filter(**{TASK_FIELDS[task]: True})


def order_task_counts(orders, store_id=None):
    """One aggregate statement, irrespective of the member's order count."""
    return with_order_tasks(orders, store_id=store_id).aggregate(
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
