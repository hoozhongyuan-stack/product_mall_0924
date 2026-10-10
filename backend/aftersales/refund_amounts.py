"""Goods remain line allocations; order shipping is a separate immutable claim."""
from django.db import transaction
from django.db.models import Sum, Q, F
from django.db.models.functions import Coalesce
from fulfillment.store_service import physical_handoff_fact
from .models import AfterSaleCase, RefundAmountSnapshot, OrderShippingRefundClaim
from .returns import effective_refund


def _eligible_shipping(order, case):
    if not order.shipping_fee_fen or case.order_line.fulfillment_kind != "SHIP" or physical_handoff_fact(order) is not None:
        return 0
    if OrderShippingRefundClaim.objects.filter(order=order).exists():
        return 0
    lines = order.lines.filter(fulfillment_kind="SHIP").annotate(
        approved_qty=Coalesce(Sum(Coalesce("aftersale_cases__return_acceptance__refund_quantity","aftersale_cases__quantity"),
            filter=Q(aftersale_cases__status__in=["WAITING_REFUND","COMPLETED"])),0))
    if lines.exclude(approved_qty=F("quantity")).exists():
        return 0
    return order.shipping_fee_fen


def freeze_refund_amount_locked(order, case):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Refund amount snapshot requires Order lock and transaction")
    prior = RefundAmountSnapshot.objects.filter(case=case).first()
    if prior:
        return prior
    shipping = _eligible_shipping(order,case)
    if shipping:
        OrderShippingRefundClaim.objects.create(order=order,case=case,amount_fen=shipping)
    goods = effective_refund(case)[1]
    return RefundAmountSnapshot.objects.create(case=case,goods_fen=goods,shipping_fen=shipping,total_fen=goods+shipping)


def amount_components(case):
    row = RefundAmountSnapshot.objects.filter(case=case).first()
    goods = effective_refund(case)[1]
    return {"goodsRefundAmountFen":row.goods_fen if row else goods,
            "shippingRefundAmountFen":row.shipping_fen if row else 0,
            "totalRefundAmountFen":row.total_fen if row else goods}


def total_refund(case):
    return amount_components(case)["totalRefundAmountFen"]


def completed_refund_summary(order_id):
    """Public authoritative goods totals; benefit coordinator does not read our tables."""
    rows = AfterSaleCase.objects.filter(order_line__order_id=order_id,status="COMPLETED").values("order_line_id").annotate(
        quantity=Sum(Coalesce("return_acceptance__refund_quantity","quantity")),
        amount=Sum(Coalesce("return_acceptance__refund_amount_fen","amount_fen")))
    return [{"lineId":str(row["order_line_id"]),"quantity":row["quantity"],"amountFen":row["amount"]} for row in rows]


def completed_shipping_refund_fen(order_id):
    return OrderShippingRefundClaim.objects.filter(order_id=order_id,case__status="COMPLETED",case__refund_intent__status="SUCCEEDED").aggregate(total=Sum("amount_fen"))["total"] or 0
