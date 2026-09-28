"""Member-owned coupon entitlements and expiring point lots.

Order IDs are UUIDs to keep this domain independent of the order module.
Orders coordinate the cross-domain transaction.
"""

import uuid

from django.db import models
from django.db.models import F, Q


class CouponCampaign(models.Model):
    class State(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        LEGACY = "LEGACY", "Legacy"

    class ClaimMode(models.TextChoices):
        SELF = "SELF", "Self"
        ADMIN = "ADMIN", "Admin"
        BOTH = "BOTH", "Both"

    class Kind(models.TextChoices):
        FULL_REDUCTION = "FULL_REDUCTION", "Full reduction"
        CASH = "CASH", "Cash"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    min_goods_fen = models.BigIntegerField(default=0)
    discount_fen = models.BigIntegerField()
    product_ids = models.JSONField(default=list)
    redeem_eligible = models.BooleanField(default=False)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    active = models.BooleanField(default=True)
    status = models.CharField(max_length=9, choices=State.choices, default=State.LEGACY)
    revision = models.PositiveIntegerField(default=1)
    total_quantity = models.PositiveIntegerField(default=0)
    issued_quantity = models.PositiveIntegerField(default=0)
    self_claim_limit = models.PositiveIntegerField(default=1)
    claim_mode = models.CharField(max_length=5, choices=ClaimMode.choices, default=ClaimMode.BOTH)
    issuance_enabled = models.BooleanField(default=False)

    class Meta:
        db_table = "benefit_coupon_campaign"
        indexes = [models.Index(fields=["status", "issuance_enabled", "valid_until"], name="coupon_distribution_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["DRAFT", "PUBLISHED", "LEGACY"]), name="coupon_state_valid"),
            models.CheckConstraint(condition=Q(claim_mode__in=["SELF", "ADMIN", "BOTH"]), name="coupon_claim_mode_valid"),
            models.CheckConstraint(condition=Q(issued_quantity__lte=F("total_quantity")), name="coupon_quantity_conserved"),
            models.CheckConstraint(condition=Q(self_claim_limit__gte=1), name="coupon_self_limit_positive"),
            models.CheckConstraint(condition=Q(status="LEGACY") | Q(total_quantity__gte=1), name="coupon_issuable_quantity_positive"),
            models.CheckConstraint(condition=Q(min_goods_fen__gte=0), name="coupon_min_nonnegative"),
            models.CheckConstraint(condition=Q(discount_fen__gt=0), name="coupon_discount_positive"),
            models.CheckConstraint(condition=Q(valid_until__gt=F("valid_from")), name="coupon_valid_window"),
            models.CheckConstraint(condition=(Q(kind="FULL_REDUCTION", min_goods_fen__gt=0) |
                                              Q(kind="CASH", min_goods_fen=0)),
                                   name="coupon_kind_threshold_valid"),
        ]


class MemberCoupon(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        RESERVED = "RESERVED", "Reserved"
        USED = "USED", "Used"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    campaign = models.ForeignKey(CouponCampaign, on_delete=models.PROTECT)
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.AVAILABLE)
    reserved_order_id = models.UUIDField(null=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    reserved_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    restored_valid_until = models.DateTimeField(null=True, blank=True)
    issuance = models.ForeignKey("CouponIssuance", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        db_table = "benefit_member_coupon"
        indexes = [models.Index(fields=["member", "status", "issued_at"], name="coupon_member_status_idx")]
        constraints = [
            models.UniqueConstraint(fields=["reserved_order_id"],
                                    condition=Q(reserved_order_id__isnull=False),
                                    name="coupon_one_per_order"),
            models.CheckConstraint(
                condition=(Q(status="AVAILABLE", reserved_order_id__isnull=True) |
                           Q(status__in=["RESERVED", "USED"], reserved_order_id__isnull=False)),
                name="coupon_order_status_consistent"),
        ]


class CouponEvent(models.Model):
    class Kind(models.TextChoices):
        RESERVE = "RESERVE", "Reserve"
        RELEASE = "RELEASE", "Release"
        CONSUME = "CONSUME", "Consume"
        RESTORE = "RESTORE", "Restore"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    coupon = models.ForeignKey(MemberCoupon, on_delete=models.PROTECT)
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    order_id = models.UUIDField()
    kind = models.CharField(max_length=7, choices=Kind.choices)
    discount_fen = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "benefit_coupon_event"
        indexes = [models.Index(fields=["member", "-created_at"], name="coupon_member_event_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(discount_fen__gt=0), name="coupon_event_discount_positive"),
            models.UniqueConstraint(fields=["coupon", "order_id", "kind"],
                                    name="coupon_once_per_transition"),
        ]


class PointsAccount(models.Model):
    """One authoritative settled/frozen balance per member.

    Lots remain the expiry source. The account can become negative after future
    refund clawbacks, while frozen points must always stay nonnegative.
    """

    member = models.OneToOneField("customers.Member", primary_key=True, on_delete=models.PROTECT,
                                  related_name="points_account")
    settled_points = models.BigIntegerField(default=0)
    frozen_points = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "benefit_points_account"
        constraints = [models.CheckConstraint(condition=Q(frozen_points__gte=0),
                                              name="points_account_frozen_nonnegative")]


class PointsGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    source_ref = models.CharField(max_length=120, unique=True)
    original_points = models.BigIntegerField()
    available_points = models.BigIntegerField()
    reserved_points = models.BigIntegerField(default=0)
    consumed_points = models.BigIntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "benefit_points_grant"
        indexes = [models.Index(fields=["member", "expires_at", "id"], name="points_member_expiry_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(original_points__gt=0), name="points_original_positive"),
            models.CheckConstraint(condition=Q(available_points__gte=0), name="points_available_nonnegative"),
            models.CheckConstraint(condition=Q(reserved_points__gte=0), name="points_reserved_nonnegative"),
            models.CheckConstraint(condition=Q(consumed_points__gte=0), name="points_consumed_nonnegative"),
            models.CheckConstraint(
                condition=Q(original_points=F("available_points") + F("reserved_points") +
                            F("consumed_points")), name="points_grant_conserved"),
        ]


class PointsReservation(models.Model):
    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reserved"
        RELEASED = "RELEASED", "Released"
        CONSUMED = "CONSUMED", "Consumed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    grant = models.ForeignKey(PointsGrant, on_delete=models.PROTECT)
    order_id = models.UUIDField()
    amount = models.BigIntegerField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.RESERVED)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "benefit_points_reservation"
        indexes = [models.Index(fields=["order_id", "status"], name="points_order_status_idx")]
        constraints = [
            models.UniqueConstraint(fields=["order_id", "grant"], name="points_order_grant_unique"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="points_reservation_positive"),
        ]


class PointsEvent(models.Model):
    class Kind(models.TextChoices):
        GRANT = "GRANT", "Grant"
        RESERVE = "RESERVE", "Reserve"
        RELEASE = "RELEASE", "Release"
        CONSUME = "CONSUME", "Consume"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    grant = models.ForeignKey(PointsGrant, on_delete=models.PROTECT)
    order_id = models.UUIDField(null=True, blank=True)
    kind = models.CharField(max_length=7, choices=Kind.choices)
    amount = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "benefit_points_event"
        indexes = [models.Index(fields=["member", "-created_at"], name="points_member_event_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="points_event_positive"),
            models.UniqueConstraint(fields=["grant", "order_id", "kind"],
                                    condition=Q(order_id__isnull=False), name="points_once_per_transition"),
        ]

from .lifecycle_models import (BenefitLedger, OrderBenefitSettlement, OrderBenefitSnapshot,
                               PointsPolicy)  # noqa: E402,F401

from .coupon_models import CouponAllocation, CouponIssuance, CouponOperation  # noqa: E402,F401
