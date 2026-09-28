"""Batch summaries keep order lists bounded by one aggregate query per domain."""
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from .models import AfterSaleAllocation, AfterSaleCase
from .service import ACTIVE


def summaries(order_ids):
    rows = AfterSaleCase.objects.filter(order_line__order_id__in=order_ids).values("order_line__order_id").annotate(
        active=Count("id", filter=Q(status__in=ACTIVE)),
        refunded=Sum(Coalesce("return_acceptance__refund_quantity","quantity"), filter=Q(status="COMPLETED")),
        amount=Sum(Coalesce("return_acceptance__refund_amount_fen","amount_fen"), filter=Q(status="COMPLETED")))
    return {row["order_line__order_id"]: {"activeCount": row["active"], "refundedQuantity": row["refunded"] or 0,
            "refundedFen": row["amount"] or 0} for row in rows}


def line_summaries(line_ids):
    rows = AfterSaleCase.objects.filter(order_line_id__in=line_ids).values("order_line_id").annotate(
        reserved=Sum(Coalesce("return_acceptance__refund_quantity","quantity"), filter=Q(status__in=ACTIVE)),
        refunded=Sum(Coalesce("return_acceptance__refund_quantity","quantity"), filter=Q(status="COMPLETED")),
        unredeemed=Sum("unredeemed_quantity", filter=Q(status__in=ACTIVE)))
    return {row["order_line_id"]: {"reservedQuantity": row["reserved"] or 0,
            "refundedQuantity": row["refunded"] or 0,
            "reservedUnredeemedQuantity": row["unredeemed"] or 0} for row in rows}


def benefit_refund_facts(order_id):
    from .refund_amounts import completed_refund_summary
    return {"active":AfterSaleCase.objects.filter(order_line__order_id=order_id,status__in=ACTIVE).exists(),
            "refunds":completed_refund_summary(order_id)}
