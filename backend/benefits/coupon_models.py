"""Distribution counters and immutable, scoped recovery receipts."""
import uuid
from django.db import models
from django.db.models import Q


class CouponAllocation(models.Model):
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    campaign = models.ForeignKey('benefits.CouponCampaign', on_delete=models.PROTECT)
    self_count = models.PositiveIntegerField(default=0)
    admin_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'benefit_coupon_allocation'
        constraints = [models.UniqueConstraint(fields=['member', 'campaign'], name='coupon_allocation_unique')]


class CouponIssuance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    campaign = models.ForeignKey('benefits.CouponCampaign', on_delete=models.PROTECT)
    actor = models.ForeignKey('accounts.AdminAccount', null=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=5)
    request_key = models.UUIDField()
    body_hash = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=200, blank=True)
    requires_repeat = models.BooleanField(default=False)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'benefit_coupon_issuance'
        indexes = [models.Index(fields=['campaign', '-created_at'], name='coupon_issuance_campaign_idx'),
                   models.Index(fields=['campaign','member','kind'],name='coupon_issuance_member_idx')]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=['SELF','ADMIN']), name='coupon_issuance_kind_valid'),
            models.CheckConstraint(condition=Q(quantity__gte=1, quantity__lte=100), name='coupon_issuance_quantity_valid'),
            models.UniqueConstraint(fields=['member','request_key'], condition=Q(kind='SELF'), name='coupon_member_claim_key_unique'),
            models.UniqueConstraint(fields=['actor','request_key'], condition=Q(kind='ADMIN'), name='coupon_actor_issue_key_unique'),
            models.CheckConstraint(condition=Q(kind='SELF', actor__isnull=True) | Q(kind='ADMIN',actor__isnull=False), name='coupon_issuance_actor_valid'),
        ]


class CouponOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey('accounts.AdminAccount', on_delete=models.PROTECT)
    request_key = models.UUIDField()
    action = models.CharField(max_length=40)
    campaign = models.ForeignKey('benefits.CouponCampaign', on_delete=models.PROTECT)
    body_hash = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'benefit_coupon_operation'
        constraints = [models.UniqueConstraint(fields=['actor','request_key'], name='coupon_operation_actor_key_unique')]
