"""Bounded fulfillment read model shared with member and PC order detail."""
from django.utils import timezone
from django.db.models import F, Window
from django.db.models.functions import RowNumber
from orders.models import OrderLine
from .codes import voucher_code
from .models import RedeemEvent, RedeemVoucher, Shipment, StoreDelivery
from .store_service import delivery_data, local_status
from .qr import voucher_qr_data_url
from .physical import page_physical_quantities
from .redemption import scope_data, permitted, event_data


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
    shipments = list(Shipment.objects.select_related("warehouse", "shipped_by").filter(order=order).order_by("-shipped_at", "-id"))
    shipment = shipments[0] if shipments else None
    shipping_lines = [line for line in lines if line.fulfillment_kind == 'SHIP']
    physical = page_physical_quantities([line.id for line in shipping_lines])
    from inventory.models import Warehouse
    default_warehouse = Warehouse.objects.filter(is_default=True, enabled=True).values_list('id', flat=True).first() if shipping_lines else None
    local = StoreDelivery.objects.filter(order=order).first() if getattr(order, "delivery_mode", "") in ("PICKUP", "DELIVERY") else None
    events = {voucher.id: [] for voucher in vouchers.values()}
    if vouchers:
        # PostgreSQL window keeps one bounded query for every voucher on the order.
        recent = (RedeemEvent.objects.filter(voucher_id__in=events)
                  .select_related("reversal", "actor", "store_staff_actor", "store")
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
        if line.fulfillment_kind == 'SHIP':
            quantities = physical[line.id]
            dispatched, delivered = quantities['dispatchedQuantity'], quantities['deliveredQuantity']
            frozen = sale.get('reservedUnshippedQuantity', 0)
            unshipped_refunded = sale.get('refundedUnshippedQuantity', 0)
            available = max(0, line.quantity - dispatched - unshipped_refunded - frozen)
            remaining = max(0, line.quantity - delivered - unshipped_refunded)
            is_local = getattr(order, 'delivery_mode', '') in ('PICKUP', 'DELIVERY')
            state = ('WAITING_PAYMENT' if order.status == 'PENDING_PAYMENT' else 'CLOSED' if order.status == 'CLOSED' else
                     'REFUNDED' if refunded >= line.quantity else 'COMPLETED' if remaining == 0 else
                     local_status(order.delivery_mode, local) if is_local else
                     'IN_TRANSIT' if dispatched > delivered else 'WAITING_SHIPMENT')
            if is_local and local and local.status == 'COMPLETED' and remaining > 0:
                state = 'WAITING_PREPARATION' if available > 0 else 'AFTER_SALE'
            elif not is_local and remaining > 0 and dispatched == delivered and frozen > 0 and available == 0:
                state = 'AFTER_SALE'
            result[str(line.id)] = {'kind': 'SHIP', 'status': state, 'refundedQuantity': refunded,
                'heldQuantity': sale.get('reservedQuantity', 0), 'frozenQuantity': frozen,
                'availableQuantity': available, 'dispatchedQuantity': dispatched, 'deliveredQuantity': delivered,
                'remainingPhysicalQuantity': remaining, 'canFulfill': order.status == 'PAID' and available > 0}
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
        history = [event_data(event) for event in events.get(voucher.id, [])] if voucher else []
        item = {"kind": "REDEEM", "status": status,
                "voucherId": str(voucher.id) if voucher else None,
                "validUntil": valid_until.isoformat() if valid_until else None,
                "redeemedQuantity": redeemed, "voidedQuantity": voided,
                "remainingQuantity": remaining, "heldQuantity": held,
                "availableQuantity": max(0, remaining-held), "frozenQuantity": held, "refundedQuantity": refunded,
                'voucherRevision': voucher.revision if voucher else None, **scope_data(line),
                'canRedeemPlatform': bool(voucher and order.status == 'PAID' and valid_until and valid_until >= today and remaining > held and permitted(line)),
                'canRedeemStore': bool(voucher and order.status == 'PAID' and valid_until and valid_until >= today and remaining > held and scope_data(line)['redeemScope'] in ('STORE', 'BOTH')),
                "events": history, "records": history}
        if include_code:
            code = voucher_code(line.id, voucher.nonce) if voucher and order.status == "PAID" and valid_until and valid_until >= today and remaining > held else None
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
    elif local and local.status == "EXCEPTION":
        status = "EXCEPTION"
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
    ship_eligible = order.status == "PAID" and not order.store_id and default_warehouse is not None and all(line.warehouse_id == default_warehouse for line in lines if line.fulfillment_kind == "SHIP") and any(item["kind"] == "SHIP" and item["availableQuantity"] > 0 for item in result.values())
    return {"shipEligible": ship_eligible, "canFulfill": bool(order.status == "PAID" and any(item.get("canFulfill") for item in result.values())), "storeDelivery": delivery_data(order, include_code=include_code), "fulfillmentStatus": status, "shipment": _shipment_data(shipment), "shipments": [_shipment_data(row) for row in shipments], "items": result,
            "afterSaleSummary": aftersale}


def page_fulfillment_summaries(orders, store_id=None):
    """Fixed queries for the entire page; capabilities use each line's own facts."""
    order_ids = [order.id for order in orders]
    if not order_ids:
        return {}
    lines = list(OrderLine.objects.filter(order_id__in=order_ids)
                 .values("id", "order_id", "fulfillment_kind", "quantity", "warehouse_id", "redeem_scope_snapshot"))
    shipping_lines = [line for line in lines if line['fulfillment_kind'] == 'SHIP']
    physical = page_physical_quantities([line['id'] for line in shipping_lines])
    vouchers = {row["order_line_id"]: row for row in RedeemVoucher.objects.filter(
        order_line_id__in=[line["id"] for line in lines]).values(
            "order_line_id", "redeemed_quantity", "voided_quantity", "valid_until")}
    shipments = {row["order_id"]: row for row in Shipment.objects.filter(order_id__in=order_ids).values(
        "order_id", "confirmed_at").order_by("shipped_at", "id")}
    local_ids = [order.id for order in orders if getattr(order, "delivery_mode", "") in ("PICKUP", "DELIVERY")]
    local_deliveries = {row.order_id: row for row in StoreDelivery.objects.filter(order_id__in=local_ids)} if local_ids else {}
    from aftersales.read import summaries, line_summaries
    aftersales = summaries(order_ids)
    holds = line_summaries([line["id"] for line in lines])
    by_order = {order_id: [] for order_id in order_ids}
    for line in lines:
        by_order[line["order_id"]].append(line)
    from inventory.models import Warehouse
    default_warehouse = Warehouse.objects.filter(is_default=True, enabled=True).values_list('id', flat=True).first() if shipping_lines else None
    today = timezone.localdate()
    result = {}
    for order in orders:
        rows = by_order[order.id]
        shipment = shipments.get(order.id)
        sale = aftersales.get(order.id, {"activeCount": 0, "refundedQuantity": 0, "refundedFen": 0})
        shipping_rows = [line for line in rows if line["fulfillment_kind"] == "SHIP"]
        has_ship = any(line["quantity"] > physical[line["id"]]["dispatchedQuantity"] + holds.get(line["id"], {}).get("refundedUnshippedQuantity", 0) + holds.get(line["id"], {}).get("reservedUnshippedQuantity", 0)
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
                if line['fulfillment_kind'] == 'SHIP':
                    facts = physical[line['id']]
                    remaining = line['quantity'] - facts['deliveredQuantity'] - holds.get(line['id'], {}).get('refundedUnshippedQuantity', 0)
                    local = local_deliveries.get(order.id)
                    available = line['quantity'] - facts['dispatchedQuantity'] - holds.get(line['id'], {}).get('refundedUnshippedQuantity', 0) - holds.get(line['id'], {}).get('reservedUnshippedQuantity', 0)
                    states.append('COMPLETED' if remaining <= 0 else
                        ('WAITING_PREPARATION' if available > 0 else 'AFTER_SALE') if local and local.status == 'COMPLETED' else
                        local_status(order.delivery_mode, local) if getattr(order, 'delivery_mode', '') in ('PICKUP', 'DELIVERY') else
                        'IN_TRANSIT' if facts['dispatchedQuantity'] > facts['deliveredQuantity'] else 'WAITING_SHIPMENT')
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
                     "EXCEPTION" if "EXCEPTION" in states else
                     "DELIVERING" if getattr(order, "delivery_mode", "") == "DELIVERY" and local_deliveries.get(order.id) and local_deliveries[order.id].status == "IN_TRANSIT" else
                     "IN_PROGRESS" if any(value in ("IN_TRANSIT", "PARTIAL", "COMPLETED") for value in states) else
                     "WAITING_SHIPMENT" if "WAITING_SHIPMENT" in states else
                     next((value for value in states if value in ("WAITING_PREPARATION", "WAITING_PICKUP", "WAITING_DELIVERY")), "WAITING_REDEMPTION"))
        redeemable = False
        if order.status == 'PAID':
            for line in rows:
                voucher = vouchers.get(line['id'])
                if line['fulfillment_kind'] != 'REDEEM' or not voucher or not voucher['valid_until'] or voucher['valid_until'] < today:
                    continue
                available = (line['quantity'] - voucher['redeemed_quantity'] - voucher['voided_quantity']
                             - holds.get(line['id'], {}).get('reservedUnredeemedQuantity', 0))
                snapshot = line['redeem_scope_snapshot'] or {}
                scope_permitted = (snapshot.get('scope', 'PLATFORM') in ('PLATFORM', 'BOTH') if store_id is None else snapshot.get('scope') in ('STORE', 'BOTH') and str(store_id) in [row['storeId'] for row in snapshot.get('stores', [])])
                if available > 0 and scope_permitted:
                    redeemable = True
                    break
        result[order.id] = {"fulfillmentStatus": state,
                            "redeemEligible": redeemable, "hasShipment": shipment is not None,
                            "shipEligible": order.status == "PAID" and has_ship and order.store_id is None and default_warehouse is not None and all(line["warehouse_id"] == default_warehouse for line in shipping_rows) and getattr(order, "delivery_mode", "") not in ("PICKUP", "DELIVERY"),
                            "canFulfill": bool(order.status == "PAID" and (has_ship or (local_deliveries.get(order.id) and local_deliveries[order.id].status in ("IN_TRANSIT", "EXCEPTION")))),
                            "afterSaleSummary": sale}
    return result


def completion_fact(order):
    """Bounded fulfillment fact; excludes DTO history and private voucher codes."""
    if order.status != "PAID":
        return False
    from aftersales.read import line_summaries
    lines = list(order.lines.order_by("id"))
    refunds = line_summaries([line.id for line in lines])
    physical = page_physical_quantities([line.id for line in lines if line.fulfillment_kind == 'SHIP'])
    from aftersales.models import AfterSaleCase
    from aftersales.service import ACTIVE
    if AfterSaleCase.objects.filter(order_line__order=order, status__in=ACTIVE).exists():
        return False
    vouchers = {row["order_line_id"]: row for row in RedeemVoucher.objects.filter(
        order_line_id__in=[line.id for line in lines]).values(
        "order_line_id", "redeemed_quantity", "voided_quantity")}
    for line in lines:
        refunded = refunds.get(line.id, {}).get("refundedQuantity", 0)
        if refunded >= line.quantity:
            continue
        if line.fulfillment_kind == "SHIP":
            if physical[line.id]["deliveredQuantity"] + refunds.get(line.id, {}).get("refundedUnshippedQuantity", 0) < line.quantity:
                return False
        else:
            voucher = vouchers.get(line.id)
            if voucher is None or voucher["redeemed_quantity"] + voucher["voided_quantity"] < line.quantity:
                return False
    return bool(lines)
