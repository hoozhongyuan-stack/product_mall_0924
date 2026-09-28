"""D0 refund intentions, immutable money facts and recovery history."""
import uuid
from django.db import models
from django.db.models import Q


class RefundIntent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField("aftersales.AfterSaleCase", on_delete=models.PROTECT, related_name="refund_intent")
    receipt = models.ForeignKey("payments.PaymentReceipt", on_delete=models.PROTECT)
    refund_no = models.CharField(max_length=40, unique=True)
    channel = models.CharField(max_length=8)
    merchant_account_id = models.CharField(max_length=80)
    original_trade_no = models.CharField(max_length=128)
    amount_fen = models.BigIntegerField()
    created_by = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    request_key = models.UUIDField()
    status = models.CharField(max_length=12, default="PREPARED")
    active_operation_id = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    succeeded_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "refund_intent"
        indexes = [models.Index(fields=["status", "created_at"], name="refund_recovery_queue_idx")]
        constraints = [
            models.UniqueConstraint(fields=["created_by", "request_key"], name="refund_actor_key_unique"),
            models.CheckConstraint(condition=Q(amount_fen__gt=0), name="refund_intent_amount_positive"),
            models.CheckConstraint(condition=Q(channel__in=["OFFLINE", "WECHAT"]), name="refund_channel_valid"),
            models.CheckConstraint(condition=Q(status__in=["PREPARED", "PROCESSING", "UNKNOWN", "FAILED", "SUCCEEDED"]),
                                   name="refund_intent_status_valid"),
            models.CheckConstraint(condition=Q(status="SUCCEEDED", succeeded_at__isnull=False) |
                                   (~Q(status="SUCCEEDED") & Q(succeeded_at__isnull=True)), name="refund_success_time"),
            models.CheckConstraint(condition=Q(active_operation_id__isnull=True, lease_until__isnull=True) |
                                   Q(active_operation_id__isnull=False, lease_until__isnull=False), name="refund_lease_shape"),
        ]


class RefundOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.ForeignKey(RefundIntent, on_delete=models.PROTECT, related_name="operations")
    kind = models.CharField(max_length=8)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True)
    outcome = models.CharField(max_length=12, default="PENDING")
    failure_code = models.CharField(max_length=40, blank=True)

    class Meta:
        db_table = "refund_operation"
        constraints = [models.CheckConstraint(condition=Q(kind__in=["DISPATCH", "QUERY"]), name="refund_operation_kind"),
                       models.CheckConstraint(condition=Q(outcome__in=["PENDING", "UNKNOWN", "FAILED", "PROCESSING", "STALE"]),
                                              name="refund_operation_outcome"),
                       models.CheckConstraint(condition=Q(outcome="PENDING", finished_at__isnull=True) |
                                              (~Q(outcome="PENDING") & Q(finished_at__isnull=False)),
                                              name="refund_operation_time")]


class RefundEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.ForeignKey(RefundIntent, on_delete=models.PROTECT, related_name="evidence")
    channel = models.CharField(max_length=8)
    merchant_account_id = models.CharField(max_length=80)
    original_trade_no = models.CharField(max_length=128)
    external_refund_no = models.CharField(max_length=128)
    amount_fen = models.BigIntegerField()
    refunded_at = models.DateTimeField()
    source = models.CharField(max_length=24)
    confirmed_by = models.ForeignKey("accounts.AdminAccount", null=True, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "refund_evidence"
        constraints = [models.UniqueConstraint(fields=["channel", "merchant_account_id", "external_refund_no"],
                                               name="refund_external_identity_unique"),
                       models.UniqueConstraint(fields=["intent"], condition=Q(applied_at__isnull=False),
                                               name="refund_intent_once_applied"),
                       models.CheckConstraint(condition=Q(amount_fen__gt=0), name="refund_evidence_positive"),
                       models.CheckConstraint(condition=Q(channel="OFFLINE", source="OFFLINE_RECONCILIATION", confirmed_by__isnull=False) |
                                              Q(channel="WECHAT", source__in=["WECHAT_NOTIFICATION", "WECHAT_QUERY"], confirmed_by__isnull=True),
                                              name="refund_evidence_source_shape")]


class RefundNotification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence = models.ForeignKey(RefundEvidence, on_delete=models.PROTECT)
    channel = models.CharField(max_length=8)
    merchant_account_id = models.CharField(max_length=80)
    event_id = models.CharField(max_length=128)
    evidence_digest = models.CharField(max_length=64)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "refund_notification"
        constraints = [models.UniqueConstraint(fields=["channel", "merchant_account_id", "event_id"],
                                               name="refund_notify_identity_unique")]


class RefundAnomaly(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence = models.OneToOneField(RefundEvidence, on_delete=models.PROTECT, related_name="anomaly")
    reason = models.CharField(max_length=24)
    open = models.BooleanField(default=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "refund_anomaly"
        constraints = [models.CheckConstraint(condition=Q(open=True, resolved_at__isnull=True) |
                                              Q(open=False, resolved_at__isnull=False), name="refund_anomaly_state_time")]


class RefundHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.ForeignKey(RefundIntent, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=24)
    evidence = models.ForeignKey(RefundEvidence, null=True, on_delete=models.PROTECT)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "refund_history"
