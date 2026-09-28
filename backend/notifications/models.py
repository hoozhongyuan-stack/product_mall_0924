"""Durable subscription-message decisions and dispatch attempts.

No row in this module establishes a real WeChat delivery fact. E2.0 only
supports an explicitly synthetic sender; a later adapter owns real receipts.
"""

import uuid

from django.db import models
from django.db.models import Q


EVENT_TYPES = ("ORDER_PAID", "ORDER_SHIPPED", "REFUND_SUCCEEDED")
EVENT_CHOICES = [(value, value) for value in EVENT_TYPES]


class SubscriptionTemplateDraft(models.Model):
    """Operator-entered candidates; deliberately disconnected from send bindings."""

    event_type = models.CharField(primary_key=True, max_length=24, choices=EVENT_CHOICES)
    draft_app_id = models.CharField(max_length=64, blank=True, default="")
    draft_template_id = models.CharField(max_length=128, blank=True, default="")
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscription_template_draft"
        constraints = [models.CheckConstraint(condition=Q(event_type__in=EVENT_TYPES),
                                              name="subscription_draft_event_valid")]


class SubscriptionTemplate(models.Model):
    """An app-specific event binding, disabled unless deliberately configured."""

    event_type = models.CharField(max_length=24, choices=EVENT_CHOICES)
    wechat_app_id = models.CharField(max_length=64)
    template_id = models.CharField(max_length=128)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscription_template"
        constraints = [models.UniqueConstraint(fields=["event_type", "wechat_app_id"],
                                            name="subscription_template_event_app_unique"),
                       models.CheckConstraint(condition=Q(enabled=False) |
                                              (~Q(template_id="") & ~Q(wechat_app_id="")),
                                              name="subscription_enabled_binding_valid")]


class MessageTask(models.Model):
    """One durable decision for one member and one immutable source fact."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=24, choices=EVENT_CHOICES)
    source_id = models.UUIDField()
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    identity_digest = models.CharField(max_length=64, blank=True)
    template_id_snapshot = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, default="BLOCKED")
    reason_code = models.CharField(max_length=32, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subscription_message_task"
        constraints = [
            models.UniqueConstraint(fields=["event_type", "source_id"],
                                    name="subscription_source_unique"),
            models.CheckConstraint(condition=Q(event_type__in=EVENT_TYPES), name="subscription_task_event_valid"),
            models.CheckConstraint(condition=Q(status__in=["BLOCKED", "READY", "RESERVED", "CLAIMED", "RETRY_WAIT",
                                                            "UNKNOWN", "FAILED", "SIMULATED"]),
                                   name="subscription_task_status_valid"),
            models.CheckConstraint(condition=Q(status="BLOCKED") |
                                   (~Q(identity_digest="") & ~Q(template_id_snapshot="")),
                                   name="subscription_send_identity_ready"),
            models.CheckConstraint(condition=Q(attempt_count__lte=3), name="subscription_attempts_bounded"),
            models.CheckConstraint(condition=(Q(status__in=["RESERVED", "CLAIMED"], lease_token__isnull=False,
                                                lease_until__isnull=False) |
                                              (~Q(status__in=["RESERVED", "CLAIMED"]) & Q(lease_token__isnull=True) &
                                               Q(lease_until__isnull=True))),
                                   name="subscription_task_lease_shape"),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at", "created_at"],
                                name="subscription_task_due_idx"),
                   models.Index(fields=["-created_at", "-id"], name="subscription_task_admin_idx")]


class SubscriptionGrant(models.Model):
    """One authorization observation; accepted grants are consumed once."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    observation_id = models.UUIDField(unique=True)
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    event_type = models.CharField(max_length=24, choices=EVENT_CHOICES)
    template_id = models.CharField(max_length=128)
    identity_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=8, choices=[("ACCEPTED", "Accepted"), ("REJECTED", "Rejected")])
    evidence_source = models.CharField(max_length=24, choices=[("SYNTHETIC_TEST", "Synthetic test"),
                                                               ("MINIPROGRAM", "Mini Program")])
    observed_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    consumed_by = models.OneToOneField(MessageTask, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="authorization_grant")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscription_grant"
        constraints = [models.CheckConstraint(condition=Q(event_type__in=EVENT_TYPES),
                                              name="subscription_grant_event_valid"),
                       models.CheckConstraint(condition=Q(state__in=["ACCEPTED", "REJECTED"]),
                                              name="subscription_grant_state_valid"),
                       models.CheckConstraint(condition=Q(evidence_source__in=["SYNTHETIC_TEST", "MINIPROGRAM"]),
                                              name="subscription_grant_source_valid"),
                       models.CheckConstraint(condition=~Q(identity_digest=""),
                                              name="subscription_grant_identity_set"),
                       models.CheckConstraint(condition=Q(expires_at__gt=models.F("observed_at")),
                                              name="subscription_grant_expiry_valid"),
                       models.CheckConstraint(condition=Q(revoked_at__isnull=True) |
                                              Q(revoked_at__gte=models.F("observed_at")),
                                              name="subscription_grant_revoke_valid")]
        indexes = [models.Index(fields=["member", "event_type", "template_id", "expires_at"],
                                name="subscription_grant_match_idx")]


class MessageAttempt(models.Model):
    """An attempt starts before I/O; an interrupted attempt remains auditable."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(MessageTask, on_delete=models.PROTECT, related_name="attempts")
    ordinal = models.PositiveSmallIntegerField()
    lease_token = models.UUIDField(unique=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=16, default="IN_FLIGHT")
    failure_code = models.CharField(max_length=32, blank=True)

    class Meta:
        db_table = "subscription_message_attempt"
        constraints = [models.UniqueConstraint(fields=["task", "ordinal"],
                                               name="subscription_attempt_ordinal_unique"),
                       models.CheckConstraint(condition=Q(outcome__in=["IN_FLIGHT", "SIMULATED", "RETRYABLE",
                                                                      "PERMANENT", "UNKNOWN"]),
                                              name="subscription_attempt_outcome_valid"),
                       models.CheckConstraint(condition=(Q(outcome="IN_FLIGHT", finished_at__isnull=True) |
                                                         (~Q(outcome="IN_FLIGHT") & Q(finished_at__isnull=False))),
                                              name="subscription_attempt_finish_shape")]
