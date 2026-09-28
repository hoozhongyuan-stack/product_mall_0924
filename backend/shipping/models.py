"""Merchant-owned delivery pricing policy."""

from django.db import models
from django.db.models import Q


class ShippingPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    fee_fen = models.PositiveIntegerField(default=1000)
    delivery_scope = models.CharField(max_length=16, default="NATIONWIDE")
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shipping_policy"
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name="shipping_policy_singleton"),
            models.CheckConstraint(condition=Q(fee_fen__lte=1_000_000), name="shipping_fee_bounded"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="shipping_revision_positive"),
            models.CheckConstraint(condition=Q(delivery_scope="NATIONWIDE"), name="shipping_scope_nationwide"),
        ]
