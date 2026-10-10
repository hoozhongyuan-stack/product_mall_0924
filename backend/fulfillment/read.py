"""Bounded fulfillment read model shared with member and PC order detail."""
from django.utils import timezone
from django.db.models import F, Window
from django.db.models.functions import RowNumber
from orders.models import OrderLine
from .codes import voucher_code
from .models import RedeemEvent, RedeemVoucher, Shipment, StoreDelivery
from .store_service import delivery_data, local_status
from .qr import voucher_qr_data_url


def _shipment_data(shipment):
    if shipment is None:
        return None
    return {
        "shipmentId": str(shipment.id),
        "carrierCode": shipment.carrier_code, "carrierName": shipment.carrier_name,
        "trackingNo": shipment.tracking_no, "warehouseId": str(shipment.warehouse_id),
        "warehouseName": shipment.warehouse.name,
        "shippedByName": shipment.shipped_by.display_name if shipment.shipped_by_id else "前置仓工作人员",
        "shippedAt": shipment.shipped_at.isoformat(),
        "autoConfirmAt": shipment.auto_confirm_at.isoformat(),
        "confirmedAt": shipment.confirmed_at.isoformat() if shipment.confirmed_at else None,
        "trackingStatus": "UNAVAILABLE",
    }


def fulfillment_data(order, *, include_code=False):
    lines = list(order.lines.order_by("id"))
    line_ids = [line.id for line in lines]
    vouchers = {voucher.order_line_id: voucher for voucher in RedeemVoucher.objects.filter(order_line_id__in=line_ids)}
    from aftersales.read import summaries, line_summaries
    aftersale = summaries([order.id]).get(order.id, {"activeCount": 0, "refundedQuantity": 0, "refundedFen": 0})
    holds = line_summaries(line_ids)
    shipment = Shipment.objects.select_related("warehouse", "shipped_by").filter(order=order).first()
    local = StoreDelivery.objects.filter(order=order).first() if getattr(order, "delivery_mode", "") in ("PICKUP", "DELIVERY") else None
    events = {voucher.id: [] for voucher in vouchers.values()}
    if vouchers:
        # PostgreSQL window keeps one bounded query for every voucher on the order.
        recent = (RedeemEvent.objects.filter(voucher_id__in=events)
                  .select_related("reversal")
                  .annotate(row_number=Window(expression=RowNumber(),
                      partition_by=[F("voucher_id")],
                      order_by=[F("occurred_at").desc(), F("id").desc()]))
                  .filter(row_number__lte=20).order_by("voucher_id", "-occurred_at", "-id"))
        for event in recent:
            events[event.voucher_id].append(event)
    result = {}
    today = timezone.localdate()
    for line in lines:
        sale = holds.get(line.id, {})
        refunded = sale.get("refundedQuantity", 0)
        held = sale.get("reservedUnredeemedQuantity", 0)
        if line.fulfillment_kind == "SHIP":
            status = ("WAITING_PAYMENT" if order.status == "PENDING_PAYMENT" else
                      "CLOSED" if order.status == "CLOSED" else
                      local_status(order.delivery_mode, local) if getattr(order, "delivery_mode", "") in ("PICKUP", "DELIVERY") else
                      "WAITING_SHIPMENT" if shipment is None else
                      "COMPLETED" if shipment.confirmed_at else "IN_TRANSIT")
            if refunded >= line.quantity:
                status = "REFUNDED"
            result[str(line.id)] = {"kind": "SHIP", "status": status,
                                    "refundedQuantity": refunded,
                                    "heldQuantity": sale.get("reservedQuantity", 0)}
            continue
        voucher = vouchers.get(line.id)
        redeemed = voucher.redeemed_quantity if voucher else 0
        voided = voucher.voided_quantity if voucher else 0
        remaining = max(0, line.quantity - redeemed - voided)
        valid_until = voucher.valid_until if voucher else getattr(line, "redeem_valid_until", None)
        status = ("WAITING_PAYMENT" if order.status == "PENDING_PAYMENT" else
                  "CLOSED" if order.status == "CLOSED" else
                  "REFUNDED" if refunded >= line.quantity or voided == line.quantity else
                  "COMPLETED" if remaining == 0 else
                  "WAITING_VALIDITY" if valid_until is None else
                  "EXPIRED" if valid_until < today else
                  "PARTIAL" if redeemed > 0 else "WAITING_REDEMPTION")
        history = [{"eventId": str(event.id), "kind": event.kind, "quantity": event.quantity,
                    "remainingQuantity": event.remaining_quantity,
                    "redeemedAt": event.occurred_at.isoformat(),
                    "reversedAt": (getattr(event, "reversal", None).occurred_at.isoformat()
                                   if event.kind == "USE" and hasattr(event, "reversal") else None)}
                   for event in events.get(voucher.id, [])] if voucher else []
        item = {"kind": "REDEEM", "status": status,
                "voucherId": str(voucher.id) if voucher else None,
                "validUntil": valid_until.isoformat() if valid_until else None,
                "redeemedQuantity": redeemed, "voidedQuantity": voided,
                "remainingQuantity": remaining, "heldQuantity": held,
                "availableQuantity": max(0, remaining-held), "refundedQuantity": refunded,
                "events": history}
        if include_code:
            code = voucher_code(line.id, voucher.nonce) if voucher and order.status == "PAID" else None
            item["voucherCode"] = code
            item["voucherQrDataUrl"] = voucher_qr_data_url(code) if code else None
        result[str(line.id)] = item
    if order.status == "PENDING_PAYMENT":
        status = "WAITING_PAYMENT"
    elif order.status == "CLOSED":
        status = "CLOSED"
    elif aftersale["activeCount"]:
        status = "AFTER_SALE"
    elif result and all(item["status"] == "REFUNDED" for item in result.values()):
        status = "REFUNDED"
    elif result and all(item["status"] in ("COMPLETED", "REFUNDED") for item in result.values()):
        status = "COMPLETED"
    elif getattr(order, "delivery_mode", "") == "DELIVERY" and local and local.status == "IN_TRANSIT":
        status = "DELIVERING"
    elif any(item["status"] in ("IN_TRANSIT", "PARTIAL", "COMPLETED") for item in result.values()):
        status = "IN_PROGRESS"
    elif any(item["status"] == "WAITING_SHIPMENT" for item in result.values()):
        status = "WAITING_SHIPMENT"
    elif any(item["status"] in ("WAITING_PREPARATION", "WAITING_PICKUP", "WAITING_DELIVERY") for item in result.values()):
        status = next(item["status"] for item in result.values() if item["kind"] == "SHIP")
    else:
        status = "WAITING_REDEMPTION"
    return {"storeDelivery": delivery_data(order, include_code=include_code), "fulfillmentStatus": status, "shipment": _shipment_data(shipment), "items": result,
            "afterSaleSummary": aftersale}


def page_fulfillment_summaries(orders):
    """Four bounded queries for a page instead of one detail query per order."""
    order_ids = [order.id for order in orders]
    if not order_ids:
        return {}
    lines = list(OrderLine.objects.filter(order_id__in=order_ids)
                 .values("id", "order_id", "fulfillment_kind", "quantity"))
    vouchers = {row["order_line_id"]: row for row in RedeemVoucher.objects.filter(
        order_line_id__in=[line["id"] for line in lines]).values(
            "order_line_id", "redeemed_quantity", "voided_quantity", "valid_until")}
    shipments = {row["order_id"]: row for row in Shipment.objects.filter(order_id__in=order_ids).values(
        "order_id", "confirmed_at")}
    local_ids = [order.id for order in orders if getattr(order, "delivery_mode", "") in ("PICKUP", "DELIVERY")]
    local_deliveries = {row.order_id: row for row in StoreDelivery.objects.filter(order_id__in=local_ids)} if local_ids else {}
    from aftersales.read import summaries, line_summaries
    aftersales = summaries(order_ids)
    holds = line_summaries([line["id"] for line in lines])
    by_order = {order_id: [] for order_id in order_ids}
    for line in lines:
        by_order[line["order_id"]].append(line)
    today = timezone.localdate()
    result = {}
    for order in orders:
        rows = by_order[order.id]
        shipment = shipments.get(order.id)
        sale = aftersales.get(order.id, {"activeCount": 0, "refundedQuantity": 0, "refundedFen": 0})
        shipping_rows = [line for line in rows if line["fulfillment_kind"] == "SHIP"]
        has_ship = any(line["quantity"] > holds.get(line["id"], {}).get("refundedQuantity", 0)
                       for line in shipping_rows)
        ship_held = any(holds.get(line["id"], {}).get("reservedQuantity", 0) for line in shipping_rows)
        if order.status == "PENDING_PAYMENT":
            state = "WAITING_PAYMENT"
        elif order.status == "CLOSED":
            state = "CLOSED"
        else:
            states = []
            for line in rows:
                if holds.get(line["id"], {}).get("refundedQuantity", 0) >= line["quantity"]:
                    states.append("REFUNDED")
                    continue
                if line["fulfillment_kind"] == "SHIP":
                    states.append(local_status(order.delivery_mode, local_deliveries.get(order.id)) if getattr(order, "delivery_mode", "") in ("PICKUP", "DELIVERY") else "WAITING_SHIPMENT" if not shipment else
                                  "COMPLETED" if shipment["confirmed_at"] else "IN_TRANSIT")
                else:
                    voucher = vouchers.get(line["id"])
                    remaining = (line["quantity"] - voucher["redeemed_quantity"] - voucher["voided_quantity"]
                                 if voucher else line["quantity"])
                    states.append("COMPLETED" if remaining == 0 else
                                  "WAITING_VALIDITY" if not voucher or not voucher["valid_until"] else
                                  "EXPIRED" if voucher["valid_until"] < today else
                                  "PARTIAL" if voucher["redeemed_quantity"] else "WAITING_REDEMPTION")
            state = ("AFTER_SALE" if sale["activeCount"] else
                     "REFUNDED" if states and all(value == "REFUNDED" for value in states) else
                     "COMPLETED" if states and all(value in ("COMPLETED", "REFUNDED") for value in states) else
                     "DELIVERING" if getattr(order, "delivery_mode", "") == "DELIVERY" and local_deliveries.get(order.id) and local_deliveries[order.id].status == "IN_TRANSIT" else
                     "IN_PROGRESS" if any(value in ("IN_TRANSIT", "PARTIAL", "COMPLETED") for value in states) else
                     "WAITING_SHIPMENT" if "WAITING_SHIPMENT" in states else
                     next((value for value in states if value in ("WAITING_PREPARATION", "WAITING_PICKUP", "WAITING_DELIVERY")), "WAITING_REDEMPTION"))
        result[order.id] = {"fulfillmentStatus": state,
                            "shipEligible": order.status == "PAID" and has_ship and not shipment and not ship_held and getattr(order, "delivery_mode", "") not in ("PICKUP", "DELIVERY"),
                            "afterSaleSummary": sale}
    return result


def completion_fact(order):
    """Bounded fulfillment fact; excludes DTO history and private voucher codes."""
    if order.status != "PAID":
        return False
    from aftersales.read import line_summaries
    lines = list(order.lines.order_by("id"))
    refunds = line_summaries([line.id for line in lines])
    from .store_service import physical_handoff_fact
    handoff = physical_handoff_fact(order)
    shipment = handoff.confirmed_at if handoff else None
    vouchers = {row["order_line_id"]: row for row in RedeemVoucher.objects.filter(
        order_line_id__in=[line.id for line in lines]).values(
        "order_line_id", "redeemed_quantity", "voided_quantity")}
    for line in lines:
        refunded = refunds.get(line.id, {}).get("refundedQuantity", 0)
        if refunded >= line.quantity:
            continue
        if line.fulfillment_kind == "SHIP":
            if shipment is None:
                return False
        else:
            voucher = vouchers.get(line.id)
            if voucher is None or voucher["redeemed_quantity"] + voucher["voided_quantity"] < line.quantity:
                return False
    return bool(lines)
