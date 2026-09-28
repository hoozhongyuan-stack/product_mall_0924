"""Historical rules, mutable projections, and immutable lifecycle events."""
import uuid
from django.db import models
from django.db.models import Q


class PointsPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    revision = models.PositiveIntegerField(default=1)
    earn_unit_fen = models.PositiveIntegerField(default=100)
    earn_points = models.PositiveIntegerField(default=1)
    deduct_points = models.PositiveIntegerField(default=100)
    deduct_fen = models.PositiveIntegerField(default=100)
    max_percent = models.PositiveSmallIntegerField(default=20)
    valid_days = models.PositiveIntegerField(default=365)
    refund_valid_days = models.PositiveIntegerField(default=30)

    class Meta:
        db_table = 'benefit_points_policy'
        constraints = [models.CheckConstraint(condition=Q(id=1), name='points_policy_singleton'),
            models.CheckConstraint(condition=Q(earn_unit_fen__gt=0, earn_points__gt=0,
                                               valid_days__gt=0, refund_valid_days__gt=0),
                                   name='points_policy_values_positive'),
            models.CheckConstraint(condition=Q(deduct_points__gt=0, deduct_fen__gt=0,
                max_percent__gte=1, max_percent__lte=100), name='points_deduct_policy_positive')]


class OrderBenefitSnapshot(models.Model):
    order_id = models.UUIDField(primary_key=True)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    policy_revision = models.PositiveIntegerField()
    earn_unit_fen = models.PositiveIntegerField()
    earn_points = models.PositiveIntegerField()
    valid_days = models.PositiveIntegerField()
    refund_valid_days = models.PositiveIntegerField()
    grade_thresholds = models.JSONField(default=list)
    line_snapshot = models.JSONField(default=list)
    coupon_id = models.UUIDField(null=True)
    coupon_valid_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'benefit_order_snapshot'
        constraints = [models.CheckConstraint(condition=Q(earn_unit_fen__gt=0, earn_points__gt=0,
                                               valid_days__gt=0, refund_valid_days__gt=0),
                                   name='points_snapshot_values_positive')]


class OrderBenefitSettlement(models.Model):
    order_id = models.UUIDField(primary_key=True)
    credited = models.BooleanField(default=False)
    earned_points = models.PositiveBigIntegerField(default=0)
    returned_points = models.PositiveBigIntegerField(default=0)
    refunded_lines = models.JSONField(default=list)
    coupon_restored = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'benefit_order_settlement'


class BenefitLedger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    order_id = models.UUIDField(null=True)
    grant = models.ForeignKey('benefits.PointsGrant', null=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    amount = models.BigIntegerField()
    balance = models.BigIntegerField()
    source_ref = models.CharField(max_length=180, unique=True)
    cause_ref = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'benefit_lifecycle_ledger'
        indexes = [models.Index(fields=['member', '-created_at'], name='benefit_lifecycle_member_idx')]
