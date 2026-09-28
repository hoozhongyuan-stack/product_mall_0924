"""Actual funds are recorded independently from order settlement."""

import uuid

from .refund_models import (RefundIntent, RefundOperation, RefundEvidence, RefundNotification,
                            RefundAnomaly, RefundHistory)

from django.db import models
from django.db.models import Q
from django.utils import timezone


class PaymentReceipt(models.Model):
    class Channel(models.TextChoices):
        WECHAT = "WECHAT", "WeChat"
        OFFLINE = "OFFLINE", "Offline"

    class Source(models.TextChoices):
        WECHAT_NOTIFICATION = "WECHAT_NOTIFICATION", "WeChat notification"
        WECHAT_QUERY = "WECHAT_QUERY", "WeChat query"
        OFFLINE_RECONCILIATION = "OFFLINE_RECONCILIATION", "Offline reconciliation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.PROTECT)
    order_no = models.CharField(max_length=40)
    channel = models.CharField(max_length=8, choices=Channel.choices)
    merchant_account_id = models.CharField(max_length=80)
    external_trade_no = models.CharField(max_length=128)
    amount_fen = models.BigIntegerField()
    paid_at = models.DateTimeField()
    source = models.CharField(max_length=24, choices=Source.choices)
    recorded_at = models.DateTimeField(auto_now_add=True)
    ready_for_settlement = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payment_receipt"
        indexes = [models.Index(fields=["order_no", "-recorded_at"], name="payment_receipt_order_idx")]
        constraints = [
            models.UniqueConstraint(fields=["channel", "merchant_account_id", "external_trade_no"],
                                    name="payment_external_trade_unique"),
            models.UniqueConstraint(fields=["order"], condition=Q(applied_at__isnull=False),
                                    name="payment_order_once_applied"),
            models.CheckConstraint(condition=Q(applied_at__isnull=True) | Q(order__isnull=False),
                                   name="payment_applied_has_order"),
            models.CheckConstraint(condition=Q(amount_fen__gt=0), name="payment_receipt_amount_positive"),
        ]


class PaymentEvent(models.Model):
    """One verified channel event identity; repeat deliveries reuse this row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt = models.ForeignKey(PaymentReceipt, on_delete=models.PROTECT, related_name="events")
    channel = models.CharField(max_length=8, choices=PaymentReceipt.Channel.choices)
    merchant_account_id = models.CharField(max_length=80)
    event_id = models.CharField(max_length=128)
    evidence_digest = models.CharField(max_length=64)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payment_event"
        constraints = [models.UniqueConstraint(fields=["channel", "merchant_account_id", "event_id"],
                                               name="payment_event_identity_unique")]


class PaymentAnomaly(models.Model):
    class Reason(models.TextChoices):
        UNKNOWN_ORDER = "UNKNOWN_ORDER", "Unknown order"
        METHOD_MISMATCH = "METHOD_MISMATCH", "Method mismatch"
        AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Amount mismatch"
        CLOSED_ORDER = "CLOSED_ORDER", "Closed order"
        ALREADY_PAID = "ALREADY_PAID", "Already paid"
        SETTLEMENT_FAILED = "SETTLEMENT_FAILED", "Settlement failed"
        IDENTIFIER_CONFLICT = "IDENTIFIER_CONFLICT", "Identifier conflict"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        RESOLVED = "RESOLVED", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receipt = models.OneToOneField(PaymentReceipt, on_delete=models.PROTECT, related_name="anomaly")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "payment_anomaly"
        indexes = [models.Index(fields=["status", "-created_at"], name="payment_anomaly_queue_idx")]
        constraints = [models.CheckConstraint(
            condition=(Q(status="OPEN", resolved_at__isnull=True) |
                       Q(status="RESOLVED", resolved_at__isnull=False)),
            name="payment_anomaly_status_time")]


class PaymentAnomalyEvent(models.Model):
    """Append-only reason/status changes keep recovery history visible."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    anomaly = models.ForeignKey(PaymentAnomaly, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=12)
    reason = models.CharField(max_length=20, choices=PaymentAnomaly.Reason.choices)
    status = models.CharField(max_length=8, choices=PaymentAnomaly.Status.choices)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payment_anomaly_event"


class OfflinePaymentPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    instructions = models.TextField(blank=True)
    merchant_account_id = models.CharField(max_length=80, blank=True)
    wechat_timeout_minutes = models.PositiveIntegerField(default=30)
    offline_timeout_minutes = models.PositiveIntegerField(default=1440)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'offline_payment_policy'
        constraints = [models.CheckConstraint(condition=Q(id=1), name='offline_policy_singleton'),
            models.CheckConstraint(condition=Q(wechat_timeout_minutes__gte=1, wechat_timeout_minutes__lte=10080,
                                               offline_timeout_minutes__gte=1, offline_timeout_minutes__lte=10080),
                                   name='payment_timeouts_bounded')]


class OfflinePaymentReport(models.Model):
    """Member statement only; never a verified funds receipt."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='payment_reports')
    key = models.UUIDField()
    note = models.CharField(max_length=500)
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'offline_payment_report'
        constraints = [models.UniqueConstraint(fields=['order', 'key'], name='offline_report_key_unique')]


class OfflineReconciliation(models.Model):
    """Immutable operator evidence, prepared before password confirmation.

    A draft is not a money fact. Authorization survives response failures so the
    same operator can retry the exact evidence without reusing a password token.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='offline_reconciliations')
    actor = models.ForeignKey('accounts.AdminAccount', on_delete=models.PROTECT)
    key = models.UUIDField()
    expected_revision = models.PositiveIntegerField()
    merchant_account_id = models.CharField(max_length=80)
    external_trade_no = models.CharField(max_length=128)
    amount_fen = models.PositiveBigIntegerField()
    paid_at = models.DateTimeField()
    note = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    authorized_at = models.DateTimeField(null=True)
    receipt = models.ForeignKey(PaymentReceipt, on_delete=models.PROTECT, null=True)
    outcome = models.CharField(max_length=16, default='DRAFT')

    class Meta:
        db_table = 'offline_reconciliation'
        indexes = [models.Index(fields=['actor', '-created_at'], name='offline_actor_created_idx')]
        constraints = [models.UniqueConstraint(fields=['actor', 'key'], name='offline_reconciliation_actor_key'),
                       models.CheckConstraint(condition=Q(amount_fen__gt=0), name='offline_reconciliation_positive'),
                       models.CheckConstraint(condition=(Q(authorized_at__isnull=True, outcome='DRAFT', receipt__isnull=True) |
                           Q(authorized_at__isnull=False, outcome__in=['AUTHORIZED', 'FAILED', 'PAID', 'ANOMALY', 'PENDING'])),
                           name='offline_authorized_state_consistent')]


class WechatPaymentAttempt(models.Model):
    """One immutable external merchant order per local order; no secrets stored."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='wechat_attempt')
    out_trade_no = models.CharField(max_length=32, unique=True)
    app_id = models.CharField(max_length=64)
    merchant_id = models.CharField(max_length=80)
    payer_openid = models.CharField(max_length=128)
    amount_fen = models.PositiveBigIntegerField()
    expires_at = models.DateTimeField()
    prepay_id = models.CharField(max_length=128, blank=True)
    prepay_expires_at = models.DateTimeField(null=True)
    state = models.CharField(max_length=16, default='NEW')
    trade_state = models.CharField(max_length=16, default='UNKNOWN')
    close_requested = models.BooleanField(default=False)
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    next_check_at = models.DateTimeField(default=timezone.now)
    last_error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wechat_payment_attempt'
        indexes = [models.Index(fields=['state', 'next_check_at'], name='wechat_reconcile_due_idx')]
        constraints = [
            models.CheckConstraint(condition=Q(amount_fen__gt=0), name='wechat_amount_positive'),
            models.CheckConstraint(condition=Q(trade_state__in=['UNKNOWN','SUCCESS','NOTPAY','USERPAYING','CLOSED','REVOKED','PAYERROR']), name='wechat_trade_state_valid'),
            models.CheckConstraint(condition=(Q(prepay_id='',prepay_expires_at__isnull=True)|(~Q(prepay_id='')&Q(prepay_expires_at__isnull=False))), name='wechat_prepay_expiry_consistent'),
            models.CheckConstraint(condition=Q(out_trade_no__regex=r'^[A-Za-z0-9_-]{6,32}$'), name='wechat_external_order_valid'),
            models.CheckConstraint(condition=Q(state__in=['NEW','PREPAYING','READY','UNKNOWN','PAID','ANOMALY','CLOSED','CLOSE_UNKNOWN']), name='wechat_attempt_valid_state'),
            models.CheckConstraint(condition=(Q(lease_token__isnull=True,lease_until__isnull=True)|Q(lease_token__isnull=False,lease_until__isnull=False)), name='wechat_lease_consistent'),
        ]


class WechatPrepayRequest(models.Model):
    """Member retry aliases all refer to the same external payment attempt."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    key = models.UUIDField()
    attempt = models.ForeignKey(WechatPaymentAttempt, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = 'wechat_prepay_request'
        constraints = [models.UniqueConstraint(fields=['member','key'], name='wechat_member_prepay_key')]


class WechatOperation(models.Model):
    """Each provider call has a durable start; bounded outcomes contain no raw data."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(WechatPaymentAttempt, on_delete=models.PROTECT, related_name='operations')
    kind = models.CharField(max_length=8, choices=[('PREPAY','Prepay'),('QUERY','Query'),('CLOSE','Close')])
    result = models.CharField(max_length=16, default='STARTED')
    error_code = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    class Meta:
        db_table = 'wechat_payment_operation'
        indexes = [models.Index(fields=['attempt','-started_at'], name='wechat_operation_recent_idx')]
        constraints = [models.CheckConstraint(condition=(Q(result='STARTED',completed_at__isnull=True)|Q(result__in=['SUCCEEDED','FAILED','SUPERSEDED'],completed_at__isnull=False)),name='wechat_operation_result_time')]

from .offline_refund_models import OfflineRefundReconciliation

from .wechat_refund_models import WechatRefundNotice, WechatRefundConflict
