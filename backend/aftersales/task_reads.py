"""Lazy after-sale facts for cross-domain task queries; no state transitions."""
from django.db.models import IntegerField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce

from .models import AfterSaleCase
from .service import ACTIVE


def active_cases():
    return AfterSaleCase.objects.filter(status__in=ACTIVE)


def with_line_quantities(lines):
    """Match read.line_summaries and guards.reserved_unredeemed_quantity in SQL."""
    cases = AfterSaleCase.objects.filter(order_line_id=OuterRef('pk')).order_by()
    refunded = cases.filter(status='COMPLETED').values('order_line_id').annotate(
        quantity=Sum(Coalesce('return_acceptance__refund_quantity', 'quantity'))).values('quantity')
    held = cases.filter(status__in=ACTIVE).values('order_line_id').annotate(
        quantity=Sum('unredeemed_quantity')).values('quantity')
    return lines.alias(
        task_refunded_quantity=Coalesce(Subquery(refunded, output_field=IntegerField()), 0),
        task_held_unredeemed=Coalesce(Subquery(held, output_field=IntegerField()), 0),
    )
