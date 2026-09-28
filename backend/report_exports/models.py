"""Durable, owner-scoped private report export requests."""

import uuid

from django.db import models
from django.db.models import Q


class ExportTask(models.Model):
    class Kind(models.TextChoices):
        AUDIT = "AUDIT", "Audit"
        BUSINESS = "BUSINESS", "Business"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"
        EXPIRED = "EXPIRED", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    created_by = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    request_key = models.UUIDField()
    filters = models.JSONField(default=dict)
    filters_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    object_key = models.CharField(max_length=160, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True)
    file_bytes = models.PositiveBigIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    failure_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "report_export_task"
        constraints = [
            models.UniqueConstraint(fields=["created_by", "request_key"], name="export_actor_request_unique"),
            models.CheckConstraint(condition=Q(kind__in=["AUDIT", "BUSINESS"]), name="export_kind_valid"),
            models.CheckConstraint(condition=Q(status__in=["PENDING", "RUNNING", "READY", "FAILED", "EXPIRED"]),
                                   name="export_status_valid"),
            models.CheckConstraint(condition=Q(attempt_count__lte=3), name="export_attempt_bounded"),
            models.CheckConstraint(condition=(Q(status="RUNNING", lease_token__isnull=False,
                                                lease_until__isnull=False) |
                                              (~Q(status="RUNNING") & Q(lease_token__isnull=True) &
                                               Q(lease_until__isnull=True))), name="export_lease_shape"),
            models.CheckConstraint(condition=~Q(status="READY") |
                                   (~Q(object_key="") & ~Q(file_sha256="") & Q(file_bytes__gt=0) &
                                    Q(completed_at__isnull=False) & Q(expires_at__isnull=False)),
                                   name="export_ready_file_shape"),
        ]
        indexes = [models.Index(fields=["status", "lease_until", "created_at"], name="export_worker_due_idx"),
                   models.Index(fields=["-created_at", "-id"], name="export_admin_page_idx"),
                   models.Index(fields=["expires_at"], name="export_expiry_idx")]
