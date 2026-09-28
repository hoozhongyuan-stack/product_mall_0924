"""Immutable provider event identities, including non-money refund outcomes."""
import uuid
from django.db import models
from django.db.models import Q


class WechatRefundNotice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.ForeignKey("payments.RefundIntent", on_delete=models.PROTECT, related_name="wechat_notices")
    merchant_id = models.CharField(max_length=80)
    event_id = models.CharField(max_length=128)
    digest = models.CharField(max_length=64)
    provider_status = models.CharField(max_length=12)
    conflict = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wechat_refund_notice"
        constraints = [models.UniqueConstraint(fields=["merchant_id", "event_id"], name="wechat_refund_notice_identity"),
            models.CheckConstraint(condition=Q(provider_status__in=["SUCCESS", "CLOSED", "ABNORMAL"]),
                                   name="wechat_refund_notice_status")]


class WechatRefundConflict(models.Model):
    """Both intents in an event collision are quarantined; facts never deleted."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notice = models.ForeignKey(WechatRefundNotice, on_delete=models.PROTECT)
    intent = models.ForeignKey("payments.RefundIntent", on_delete=models.PROTECT, related_name="wechat_conflicts")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wechat_refund_conflict"
        constraints = [models.UniqueConstraint(fields=["notice", "intent"], name="wechat_refund_conflict_once")]
