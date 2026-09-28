"""Immutable local Mini Program source snapshots and build attempt facts."""

import uuid

from django.db import models
from django.db.models import Q


class CodeVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version_label = models.CharField(max_length=40)
    source_revision = models.CharField(max_length=64, blank=True)
    source_digest = models.CharField(max_length=64, unique=True)
    package_sha256 = models.CharField(max_length=64, unique=True)
    package_bytes = models.PositiveIntegerField()
    file_count = models.PositiveIntegerField()
    object_key = models.CharField(max_length=200, unique=True)
    storage_status = models.CharField(max_length=8, default="READY")
    platform_status = models.CharField(max_length=20, default="NOT_CONFIGURED")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mini_code_version"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(storage_status="READY"), name="mini_code_storage_ready"),
            models.CheckConstraint(condition=Q(platform_status="NOT_CONFIGURED"),
                                   name="mini_code_platform_unconfigured"),
            models.CheckConstraint(condition=Q(package_bytes__gt=0), name="mini_code_package_nonempty"),
            models.CheckConstraint(condition=Q(file_count__gt=0), name="mini_code_files_nonempty"),
        ]


class CodeBuildJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(CodeVersion, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="build_jobs")
    status = models.CharField(max_length=10, default="STARTED")
    failure_code = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mini_code_build_job"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["STARTED", "SUCCEEDED", "FAILED"]),
                                   name="mini_code_job_status_valid"),
            models.CheckConstraint(condition=(Q(status="STARTED", completed_at__isnull=True,
                                                failure_code="", version__isnull=True) |
                                              Q(status="SUCCEEDED", completed_at__isnull=False,
                                                failure_code="", version__isnull=False) |
                                              Q(status="FAILED", completed_at__isnull=False,
                                                version__isnull=True) & ~Q(failure_code="")),
                                   name="mini_code_job_state_shape"),
        ]
