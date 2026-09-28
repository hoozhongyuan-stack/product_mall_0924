"""Bounded, privacy separated D1 read and eligibility DTOs."""

from django.db import transaction
from django.utils import timezone
from .models import AfterSaleCase, AfterSaleAllocation
from accounts.security import permissions
from .service import AfterSaleError, ACTIVE, _lock_line, _eligibility, _fail


def preview(member, line_id, kind, scope, quantity):
    if (
        type(quantity) is not int
        or quantity < 1
        or kind not in AfterSaleCase.Kind.values
        or (scope not in ("USED", "UNUSED"))
    ):
        raise AfterSaleError("售后类型、范围或数量不正确。")
    with transaction.atomic():
        order, line = _lock_line(line_id, member)
        if AfterSaleCase.objects.filter(order_line=line, status__in=ACTIVE).exists():
            _fail("此订单项已有进行中的售后。", "AFTERSALE_IN_PROGRESS")
        a = AfterSaleAllocation.objects.filter(order_line=line).first()
        refunded_qty = a.refunded_qty if a else 0
        refunded_fen = a.refunded_fen if a else 0
        if quantity + refunded_qty > line.quantity:
            _fail("申请数量超过可售后数量。", "QUANTITY_EXCEEDED")
        _eligibility(order, line, kind, scope, quantity)
        amount = (
            line.payable_fen * (refunded_qty + quantity) // line.quantity - refunded_fen
        )
        return {
            "lineId": str(line.id),
            "kind": kind,
            "redemptionScope": scope,
            "quantity": quantity,
            "amountFen": amount,
            "orderKind":order.order_kind,"refundPoints":quantity*line.points_unit_price,
        }


def options(member, order_id):
    from orders.models import Order
    from fulfillment.models import Shipment, RedeemVoucher

    order = Order.objects.filter(pk=order_id, member=member).first()
    if not order:
        raise AfterSaleError("订单不存在。", "ORDER_NOT_FOUND", 404)
    from django.db.models import Sum
    from payments.access import applied_receipt
    from .models import OrderAfterSaleSnapshot

    lines = list(order.lines.all()[:201])
    if len(lines) > 200:
        raise AfterSaleError(
            "订单项数量超过在线售后范围，请联系店铺。", "ORDER_ITEMS_LIMIT", 409
        )
    allocations = {
        a.order_line_id: a
        for a in AfterSaleAllocation.objects.filter(order_line__order=order)
    }
    vouchers = {
        v.order_line_id: v
        for v in RedeemVoucher.objects.filter(order_line__order=order)
    }
    active = set(
        AfterSaleCase.objects.filter(
            order_line__order=order, status__in=ACTIVE
        ).values_list("order_line_id", flat=True)
    )
    used_totals = {
        r["order_line_id"]: r["used"]
        for r in AfterSaleCase.objects.filter(
            order_line__order=order, status="COMPLETED"
        )
        .values("order_line_id")
        .annotate(used=Sum("used_quantity"))
    }
    common = {
        "snapshot": OrderAfterSaleSnapshot.objects.filter(order=order).first(),
        "receipt": applied_receipt(order),
        "shipment": Shipment.objects.filter(order=order).first(),
    }
    items = []
    for line in lines:
        item = {
            "lineId": str(line.id),
            "title": line.product_name,
            "fulfillmentKind": line.fulfillment_kind,
            "quantity": line.quantity,
            "payableFen": line.payable_fen,
            "pointsUnitPrice":line.points_unit_price,"pointsTotal":line.points_total,
            "options": [],
            "blockedCode": "",
            "blockedMessage": "",
        }
        a = allocations.get(line.id)
        remaining = line.quantity - (a.refunded_qty if a else 0)
        candidates = []
        if line.fulfillment_kind == "SHIP":
            candidates = [
                (
                    (
                        "RETURN_REFUND"
                        if common["shipment"] is not None
                        else "REFUND_ONLY"
                    ),
                    "UNUSED",
                    remaining,
                )
            ]
        else:
            v = vouchers.get(line.id)
            if v:
                used = used_totals.get(line.id, 0)
                candidates = [
                    (
                        "REFUND_ONLY",
                        "UNUSED",
                        min(
                            remaining,
                            line.quantity - v.redeemed_quantity - v.voided_quantity,
                        ),
                    ),
                    ("REFUND_ONLY", "USED", min(remaining, v.redeemed_quantity - used)),
                ]
        for kind, scope, max_qty in candidates:
            if max_qty < 1:
                continue
            try:
                if line.id in active:
                    _fail("此订单项已有进行中的售后。", "AFTERSALE_IN_PROGRESS")
                facts = {
                    **common,
                    "voucher": vouchers.get(line.id),
                    "completed_used": used_totals.get(line.id, 0),
                }
                _eligibility(order, line, kind, scope, max_qty, facts=facts)
                amount = line.payable_fen * (
                    (a.refunded_qty if a else 0) + max_qty
                ) // line.quantity - (a.refunded_fen if a else 0)
                item["options"].append(
                    {"kind": kind, "redemptionScope": scope, "maxQuantity": max_qty,"maxPoints":max_qty*line.points_unit_price}
                )
            except AfterSaleError as exc:
                item["blockedCode"], item["blockedMessage"] = (exc.code, str(exc))
        if item["options"]:
            item["blockedCode"] = item["blockedMessage"] = ""
        elif not item["blockedCode"]:
            item["blockedCode"], item["blockedMessage"] = (
                "NO_REFUNDABLE_QUANTITY",
                "没有可申请的数量。",
            )
        items.append(item)
    return {"orderId": str(order.id),"orderKind":order.order_kind,"items": items}


def case_data(case, admin=False, actor=None, detail=True):
    line = case.order_line
    order = line.order
    acceptance = getattr(case,"return_acceptance",None)
    effective_quantity = acceptance.refund_quantity if acceptance else case.quantity
    effective_amount = acceptance.refund_amount_fen if acceptance else case.amount_fen
    allocation=getattr(line,'aftersaleallocation',None)
    refunded_qty=allocation.refunded_qty if allocation else 0
    reserved_qty=allocation.reserved_qty if allocation else 0
    d = {
        "orderKind":order.order_kind,"exchangePoints":line.points_total,
        "requestedRefundPoints":case.quantity*line.points_unit_price,
        "effectiveRefundPoints":effective_quantity*line.points_unit_price,
        "pointsToReturn":effective_quantity*line.points_unit_price,
        "refundablePoints":max(0,line.quantity-refunded_qty-reserved_qty)*line.points_unit_price,
        "returnedPoints":refunded_qty*line.points_unit_price,
        "caseId": str(case.id),
        "orderId": str(order.id),
        "orderNo": order.order_no,
        "lineId": str(line.id),
        "title": line.product_name,
        "fulfillmentKind": line.fulfillment_kind,
        "kind": case.kind,
        "redemptionScope": "USED" if case.used_quantity else "UNUSED",
        "quantity": case.quantity,
        "amountFen": case.amount_fen,
        "effectiveRefundQuantity": effective_quantity,
        "effectiveRefundAmountFen": effective_amount,
        "reason": case.reason,
        "status": case.status,
        "revision": case.revision,
        "createdAt": case.created_at.isoformat(),
        "updatedAt": case.updated_at.isoformat(),
        "canWithdraw": case.status == "PENDING_REVIEW",
    }
    from .refund_amounts import amount_components
    d.update(amount_components(case))
    if detail:
        from benefits.lifecycle import order_benefit_data
        d["orderBenefits"] = order_benefit_data(order.id)
    d["canSettleBenefits"] = bool(admin and actor and {"refund.prepare","aftersale.read"}.issubset(permissions(actor)) and case.status == "WAITING_REFUND" and d["totalRefundAmountFen"] == 0)
    from payments.models import RefundIntent

    intent = RefundIntent.objects.filter(case=case).first() if detail else None
    if detail:
        from .returns import shipment_data, acceptance_data
        shipment = case.return_shipments.order_by("-revision").first()
        d["returnShipment"] = shipment_data(shipment) if shipment else None
        d["returnAcceptance"] = acceptance_data(acceptance,admin) if acceptance else None
        d["canSubmitReturnShipment"] = not admin and case.kind=="RETURN_REFUND" and case.status=="WAITING_RETURN"
        d["canAcceptReturn"] = bool(admin and actor and "aftersale.return.accept" in permissions(actor) and case.status=="WAITING_RETURN")
        d["refundStatus"] = intent.status if intent else None
        d["events"] = [
            {
                "action": e.action,
                "status": e.status,
                "reason": e.reason,
                "occurredAt": e.occurred_at.isoformat(),
            }
            for e in reversed(list(case.events.order_by("-occurred_at", "-id")[:30]))
        ]
    if admin:
        d.update(
            paymentMethod=order.payment_method,
            memberName="会员 " + str(case.member_id)[:8],
            canWithdraw=False,
        )
        if actor and {"refund.prepare", "refund.offline.confirm"} & set(
            permissions(actor)
        ):
            from payments.access import applied_receipt
            from payments.offline_refunds import reconciliation_data
            from payments.models import OfflineRefundReconciliation

            receipt = applied_receipt(order)
            d["refundSource"] = (
                {
                    "merchantAccountId": receipt.merchant_account_id,
                    "originalTradeNo": receipt.external_trade_no,
                    "amountFen": d["totalRefundAmountFen"],
                }
                if receipt
                else None
            )
            row = (
                OfflineRefundReconciliation.objects.select_related(
                    "prepared_by", "authorized_by", "intent"
                )
                .filter(intent__case=case)
                .first()
            )
            d["offlineRefund"] = reconciliation_data(row, actor) if row else None
            if receipt and receipt.channel == "WECHAT":
                from payments.wechat_refund_service import wechat_refund_data
                d["wechatRefund"] = wechat_refund_data(case,actor)
    return d


def list_cases(params, member=None, actor=None):
    if set(params) - {"page", "pageSize", "status", "orderId", "orderNo"}:
        raise AfterSaleError("筛选字段不正确。")
    try:
        page, size = (int(params.get("page", "1")), int(params.get("pageSize", "20")))
    except ValueError:
        raise AfterSaleError("分页参数不正确。")
    if not 1 <= page <= 10000 or not 1 <= size <= 100:
        raise AfterSaleError("分页超出范围。")
    rows = AfterSaleCase.objects.select_related("order_line__order", "order_line__aftersaleallocation", "member", "return_acceptance")
    if member:
        rows = rows.filter(member=member)
    status = params.get("status")
    if status:
        if status not in AfterSaleCase.Status.values:
            raise AfterSaleError("售后状态不正确。")
        rows = rows.filter(status=status)
    order_id = params.get("orderId")
    if order_id:
        from uuid import UUID

        try:
            order_id = UUID(order_id)
        except ValueError:
            raise AfterSaleError("订单标识不正确。")
        rows = rows.filter(order_line__order_id=order_id)
    search = params.get("orderNo", "")
    if len(search) > 40:
        raise AfterSaleError("订单号搜索过长。")
    if search:
        rows = rows.filter(order_line__order__order_no__icontains=search.strip())
    total = rows.count()
    return {
        "items": [
            case_data(c, admin=bool(actor), actor=None, detail=False)
            for c in rows.order_by("-created_at", "-id")[
                (page - 1) * size : page * size
            ]
        ],
        "page": page,
        "pageSize": size,
        "total": total,
    }
