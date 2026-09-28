"""Short-lived quote snapshots; they never reserve stock or authorize orders."""

import uuid

from django.db import models
from django.db.models import Q


class CheckoutQuote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member_id = models.UUIDField(null=True, blank=True)
    source_digest = models.CharField(max_length=64, blank=True)
    address_id = models.UUIDField(null=True, blank=True)
    lines = models.JSONField(default=list)
    goods_total_fen = models.BigIntegerField()
    shipping_fee_fen = models.BigIntegerField(default=0)
    shipping_policy_revision = models.PositiveIntegerField(default=1)
    coupon_id = models.UUIDField(null=True, blank=True)
    coupon_discount_fen = models.BigIntegerField(default=0)
    benefit_policy_snapshot = models.JSONField(default=dict)
    points_to_use = models.PositiveIntegerField(default=0)
    points_discount_fen = models.BigIntegerField(default=0)
    allocations = models.JSONField(default=list)
    payable_fen = models.BigIntegerField()
    ready = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "checkout_quote"
        indexes = [models.Index(fields=["source_digest", "-created_at"], name="quote_source_created_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(goods_total_fen__gte=0), name="quote_goods_nonnegative"),
            models.CheckConstraint(condition=Q(shipping_fee_fen__gte=0), name="quote_shipping_nonnegative"),
            models.CheckConstraint(condition=Q(coupon_discount_fen__gte=0), name="quote_coupon_nonnegative"),
            models.CheckConstraint(condition=Q(points_discount_fen__gte=0), name="quote_points_nonnegative"),
            models.CheckConstraint(condition=Q(coupon_discount_fen__lte=models.F("goods_total_fen")),
                                   name="quote_coupon_within_goods"),
            models.CheckConstraint(condition=Q(points_discount_fen__lte=models.F("goods_total_fen") - models.F("coupon_discount_fen")),
                                   name="quote_points_within_goods"),
            models.CheckConstraint(condition=Q(payable_fen__gte=0), name="quote_payable_nonnegative"),
            models.CheckConstraint(condition=Q(payable_fen=models.F("goods_total_fen") + models.F("shipping_fee_fen")
                                             - models.F("coupon_discount_fen") - models.F("points_discount_fen")),
                                   name="quote_total_matches_parts"),
        ]
