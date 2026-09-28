"""Immutable bank transfer facts; registration is not a money confirmation."""

import uuid
from django.db import models
from django.db.models import Q


class OfflineRefundReconciliation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.OneToOneField(
        "payments.RefundIntent",
        on_delete=models.PROTECT,
        related_name="offline_reconciliation",
    )
    prepared_by = models.ForeignKey(
        "accounts.AdminAccount",
        on_delete=models.PROTECT,
        related_name="refund_reconciliations",
    )
    request_key = models.UUIDField()
    expected_revision = models.PositiveIntegerField()
    merchant_account_id = models.CharField(max_length=80)
    external_refund_no = models.CharField(max_length=128)
    amount_fen = models.BigIntegerField()
    refunded_at = models.DateTimeField()
    refund_method = models.CharField(max_length=20)
    proof_reference = models.CharField(max_length=128)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    authorized_by = models.ForeignKey(
        "accounts.AdminAccount",
        null=True,
        on_delete=models.PROTECT,
        related_name="confirmed_refund_reconciliations",
    )
    authorized_at = models.DateTimeField(null=True)
    outcome = models.CharField(max_length=24, default="PENDING_CONFIRMATION")

    class Meta:
        db_table = "offline_refund_reconciliation"
        constraints = [
            models.CheckConstraint(
                condition=Q(outcome="PENDING_CONFIRMATION", authorized_at__isnull=True)
                | Q(
                    outcome__in=[
                        "AUTHORIZED",
                        "SUCCEEDED",
                        "SETTLEMENT_FAILED",
                        "ANOMALY",
                    ],
                    authorized_at__isnull=False,
                ),
                name="offline_refund_outcome_shape",
            ),
            models.UniqueConstraint(
                fields=["prepared_by", "request_key"], name="offline_refund_actor_key"
            ),
            models.UniqueConstraint(
                fields=["merchant_account_id", "external_refund_no"],
                name="offline_refund_transfer_unique",
            ),
            models.CheckConstraint(
                condition=Q(amount_fen__gt=0), name="offline_refund_positive"
            ),
            models.CheckConstraint(
                condition=Q(authorized_by__isnull=True, authorized_at__isnull=True)
                | Q(authorized_by__isnull=False, authorized_at__isnull=False),
                name="offline_refund_auth_shape",
            ),
            models.CheckConstraint(
                condition=Q(
                    refund_method__in=[
                        "BANK_TRANSFER",
                        "WECHAT_TRANSFER",
                        "ALIPAY_TRANSFER",
                        "CASH",
                        "OTHER",
                    ]
                ),
                name="offline_refund_method",
            ),
        ]
