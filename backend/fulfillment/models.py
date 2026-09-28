"""C3 delivery and redemption evidence; money and inventory stay in their domains."""

import uuid
from django.db import models
from django.db.models import Q, F


class Carrier(models.Model):
    code = models.CharField(primary_key=True, max_length=24)
    name = models.CharField(max_length=80)
    enabled = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fulfillment_carrier"


class FulfillmentPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    auto_confirm_days = models.PositiveSmallIntegerField(default=10)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fulfillment_policy"
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name="fulfillment_policy_singleton"),
            models.CheckConstraint(condition=Q(auto_confirm_days__gte=1, auto_confirm_days__lte=30),
                                   name="fulfillment_auto_days_bounded"),
        ]


class OrderFulfillmentSnapshot(models.Model):
    order = models.OneToOneField("orders.Order", primary_key=True, on_delete=models.PROTECT,
                                 related_name="fulfillment_snapshot")
    auto_confirm_days = models.PositiveSmallIntegerField()
    policy_revision = models.PositiveIntegerField()

    class Meta:
        db_table = "fulfillment_order_snapshot"
        constraints = [models.CheckConstraint(condition=Q(auto_confirm_days__gte=1,
                                                          auto_confirm_days__lte=30),
                                              name="fulfillment_snapshot_days_bounded")]


class Shipment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="shipment")
    carrier = models.ForeignKey(Carrier, on_delete=models.PROTECT)
    carrier_code = models.CharField(max_length=24)
    carrier_name = models.CharField(max_length=80)
    tracking_no = models.CharField(max_length=80)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT)
    shipped_by = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT, related_name="shipments")
    shipped_at = models.DateTimeField()
    auto_confirm_days_snapshot = models.PositiveSmallIntegerField()
    auto_confirm_at = models.DateTimeField(db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by_member = models.BooleanField(default=False)
    request_key = models.UUIDField(unique=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "fulfillment_shipment"
        constraints = [
            models.CheckConstraint(condition=Q(auto_confirm_days_snapshot__gte=1,
                                               auto_confirm_days_snapshot__lte=30), name="shipment_auto_days_bounded"),
            models.CheckConstraint(condition=Q(confirmed_at__isnull=True) | Q(confirmed_at__gte=F("shipped_at")),
                                   name="shipment_confirm_after_ship"),
        ]


class ShipmentCorrection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="corrections")
    old_carrier_code = models.CharField(max_length=24)
    old_carrier_name = models.CharField(max_length=80)
    old_tracking_no = models.CharField(max_length=80)
    new_carrier_code = models.CharField(max_length=24)
    new_carrier_name = models.CharField(max_length=80)
    new_tracking_no = models.CharField(max_length=80)
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    corrected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fulfillment_shipment_correction"
        indexes = [models.Index(fields=["shipment", "-corrected_at"], name="shipment_correction_recent_idx")]


class ShipmentTrackingSnapshot(models.Model):
    """Replaceable provider cache, never a shipment or delivery fact."""
    shipment = models.OneToOneField(Shipment, primary_key=True, on_delete=models.CASCADE,
                                    related_name="tracking_snapshot")
    carrier_code = models.CharField(max_length=24)
    tracking_no = models.CharField(max_length=80)
    status = models.CharField(max_length=20, default="UNAVAILABLE")
    events = models.JSONField(default=list)
    checked_at = models.DateTimeField(null=True, blank=True)
    next_check_at = models.DateTimeField()
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "fulfillment_tracking_snapshot"


class RedeemVoucher(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_line = models.OneToOneField("orders.OrderLine", on_delete=models.PROTECT, related_name="redeem_voucher")
    nonce = models.CharField(max_length=32, unique=True)
    code_digest = models.CharField(max_length=64, unique=True)
    valid_until = models.DateField(null=True, blank=True)
    redeemed_quantity = models.PositiveIntegerField(default=0)
    voided_quantity = models.PositiveIntegerField(default=0)
    revision = models.PositiveIntegerField(default=1)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fulfillment_redeem_voucher"
        constraints = [
            models.CheckConstraint(condition=Q(redeemed_quantity__gte=0), name="voucher_redeemed_nonnegative"),
            models.CheckConstraint(condition=Q(voided_quantity__gte=0), name="voucher_voided_nonnegative"),
        ]


class RedeemEvent(models.Model):
    class Kind(models.TextChoices):
        USE = "USE", "Use"
        REVERSE = "REVERSE", "Reverse"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voucher = models.ForeignKey(RedeemVoucher, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=7, choices=Kind.choices)
    quantity = models.PositiveIntegerField()
    remaining_quantity = models.PositiveIntegerField()
    actor = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    request_key = models.UUIDField(null=True, blank=True)
    original_use = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="reversal")
    reason = models.CharField(max_length=500, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fulfillment_redeem_event"
        indexes = [models.Index(fields=["voucher", "-occurred_at"], name="redeem_event_recent_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="redeem_event_quantity_positive"),
            models.UniqueConstraint(fields=["voucher", "request_key"], condition=Q(request_key__isnull=False),
                                    name="redeem_voucher_request_unique"),
            models.CheckConstraint(condition=(Q(kind="USE", request_key__isnull=False,
                                               original_use__isnull=True, reason="") |
                                              Q(kind="REVERSE", request_key__isnull=True,
                                                original_use__isnull=False)), name="redeem_event_kind_shape"),
        ]


class RedeemLookupFailure(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    source_digest = models.CharField(max_length=64)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fulfillment_redeem_lookup_failure"
        indexes = [models.Index(fields=["actor", "-occurred_at"], name="redeem_lookup_actor_idx")]


class VoucherRefundEvent(models.Model):
    """Immutable evidence of unused voucher capacity permanently retired by refund."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voucher = models.ForeignKey(RedeemVoucher, on_delete=models.PROTECT, related_name="refund_events")
    case_id = models.UUIDField(unique=True)
    quantity = models.PositiveIntegerField()
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fulfillment_voucher_refund_event"
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0),
                                              name="voucher_refund_quantity_positive")]
