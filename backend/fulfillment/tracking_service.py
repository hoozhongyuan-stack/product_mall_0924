"""Member tracking reads with bounded provider calls and isolated cache leases."""
import uuid
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Shipment, ShipmentTrackingSnapshot
from .tracking import query_kdniao


logger = logging.getLogger(__name__)


def _payload(shipment, snapshot=None):
    valid = snapshot and (snapshot.carrier_code, snapshot.tracking_no) == (
        shipment.carrier_code, shipment.tracking_no)
    return {
        "carrierCode": shipment.carrier_code,
        "carrierName": shipment.carrier_name,
        "trackingNo": shipment.tracking_no,
        "status": snapshot.status if valid else "UNAVAILABLE",
        "events": snapshot.events if valid else [],
        "checkedAt": snapshot.checked_at.isoformat() if valid and snapshot.checked_at else None,
        "retryAfter": snapshot.next_check_at.isoformat() if valid else None,
    }


def read_tracking(shipment_id):
    config = getattr(settings, "KDNIAO", {})
    business_id, app_key = config.get("EBUSINESS_ID", ""), config.get("APP_KEY", "")
    if config.get("ENABLED") is not True or not business_id or not app_key:
        return _payload(Shipment.objects.get(pk=shipment_id))
    now = timezone.now()
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().get(pk=shipment_id)
        snapshot, _ = ShipmentTrackingSnapshot.objects.select_for_update().get_or_create(
            shipment=shipment, defaults={"carrier_code": shipment.carrier_code,
                                         "tracking_no": shipment.tracking_no, "next_check_at": now})
        if (snapshot.carrier_code, snapshot.tracking_no) != (shipment.carrier_code, shipment.tracking_no):
            snapshot.carrier_code, snapshot.tracking_no = shipment.carrier_code, shipment.tracking_no
            snapshot.status, snapshot.events, snapshot.checked_at = "UNAVAILABLE", [], None
            snapshot.next_check_at, snapshot.lease_until, snapshot.lease_id = now, None, None
        if snapshot.next_check_at > now or (snapshot.lease_until and snapshot.lease_until > now):
            return _payload(shipment, snapshot)
        lease_id = uuid.uuid4()
        snapshot.lease_id, snapshot.lease_until = lease_id, now + timedelta(seconds=15)
        snapshot.save()
        carrier_code, tracking_no = shipment.carrier_code, shipment.tracking_no
    try:
        result = query_kdniao(carrier_code, tracking_no, business_id, app_key)
    except Exception as exc:  # Provider failure must not change or leak the shipment fact.
        logger.warning("Logistics query failed for shipment %s (%s)", shipment_id,
                       type(exc).__name__)
        result = {"status": "UNAVAILABLE", "events": []}
    now = timezone.now()
    with transaction.atomic():
        shipment = Shipment.objects.select_for_update().get(pk=shipment_id)
        snapshot = ShipmentTrackingSnapshot.objects.select_for_update().get(shipment_id=shipment_id)
        if (snapshot.lease_id != lease_id or
                (shipment.carrier_code, shipment.tracking_no) != (carrier_code, tracking_no)):
            return _payload(shipment)
        snapshot.status, snapshot.events = result["status"], result["events"]
        snapshot.checked_at = now
        snapshot.next_check_at = now + timedelta(minutes=1 if result["status"] == "UNAVAILABLE" else 15)
        snapshot.lease_until, snapshot.lease_id = None, None
        snapshot.save(update_fields=["status", "events", "checked_at", "next_check_at", "lease_until", "lease_id"])
        return _payload(shipment, snapshot)
