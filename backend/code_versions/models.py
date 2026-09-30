"""Immutable local Mini Program source snapshots and build attempt facts."""

import uuid

from django.conf import settings
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
    source_revision = models.CharField(max_length=64, blank=True)
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


class CodeSourceProvenance(models.Model):
    """Verified Git source for an immutable package; never rewritten."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(CodeVersion, on_delete=models.PROTECT,
                                related_name="provenances")
    source_revision = models.CharField(max_length=64, unique=True)
    verified_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mini_code_source_provenance"
        ordering = ["-verified_at", "-id"]
        indexes = [models.Index(fields=["version", "-verified_at", "-id"],
                                name="mini_code_prov_latest_idx")]


class CodeUploadKey(models.Model):
    """Encrypted CI signing key, bound to one Mini Program AppID."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    app_id = models.CharField(max_length=18, blank=True)
    encrypted_payload = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mini_code_upload_key"
        constraints = [models.CheckConstraint(condition=Q(pk=1), name="mini_code_upload_key_singleton")]


class DeveloperUploadKey(models.Model):
    """Separate credential for the component's development Mini Program."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    app_id = models.CharField(max_length=18, blank=True)
    encrypted_payload = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mini_developer_upload_key"
        constraints = [models.CheckConstraint(condition=Q(pk=1), name="mini_developer_key_singleton")]


class ReleaseUploadJob(models.Model):
    """A direct CI developer-version upload, never a review/release claim."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(CodeVersion, on_delete=models.PROTECT, related_name="upload_jobs")
    app_id = models.CharField(max_length=18)
    developer_app_id = models.CharField(max_length=18)
    channel = models.CharField(max_length=16, default="CI_DIRECT")
    package_sha256 = models.CharField(max_length=64)
    staged_digest = models.CharField(max_length=64, blank=True)
    key_revision = models.PositiveIntegerField()
    upload_version = models.CharField(max_length=40)
    description = models.CharField(max_length=100, blank=True)
    request_key = models.UUIDField(unique=True)
    request_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, default="PENDING")
    failure_code = models.CharField(max_length=40, blank=True)
    failure_stage = models.CharField(max_length=16, blank=True)
    sdk_code = models.CharField(max_length=48, blank=True)
    platform_error_code = models.IntegerField(null=True, blank=True)
    inner_platform_error_code = models.IntegerField(null=True, blank=True)
    platform_reason = models.CharField(max_length=32, blank=True)
    resolution_note = models.CharField(max_length=500, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    call_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "mini_code_upload_job"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["PENDING", "RUNNING", "SUCCEEDED",
                                                            "FAILED", "UNKNOWN", "RESOLVED"]),
                                   name="mini_code_upload_status_valid"),
            models.CheckConstraint(condition=Q(channel__in=["CI_DIRECT", "DIRECT_COMMIT"]),
                                   name="mini_code_upload_channel_valid"),
            models.UniqueConstraint(fields=["app_id"], condition=Q(status__in=["PENDING", "RUNNING", "UNKNOWN"]),
                                    name="mini_code_one_active_upload"),
        ]


class ReleaseReviewJob(models.Model):
    """Durable attempts for audit and release; UNKNOWN is never retried automatically."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.ForeignKey(ReleaseUploadJob, on_delete=models.PROTECT, related_name='review_jobs')
    app_id = models.CharField(max_length=18)
    user_version = models.CharField(max_length=40)
    item_list = models.JSONField()
    version_desc = models.CharField(max_length=200)
    request_key = models.UUIDField(unique=True)
    request_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    audit_id = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, default='SUBMITTING')
    failure_code = models.CharField(max_length=40, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    resolution_note = models.CharField(max_length=500, blank=True)
    release_requested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mini_code_review_job'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(condition=Q(status__in=['SUBMITTING', 'SUBMITTED', 'REVIEWING',
                'APPROVED', 'REJECTED', 'FAILED', 'UNKNOWN', 'RELEASING', 'RELEASE_UNKNOWN',
                'RELEASE_REQUESTED', 'CLOSED_UNVERIFIED']), name='mini_code_review_status_valid'),
            models.UniqueConstraint(fields=['app_id'], condition=Q(status__in=['SUBMITTING',
                'SUBMITTED', 'REVIEWING', 'APPROVED', 'UNKNOWN', 'RELEASING',
                'RELEASE_UNKNOWN', 'RELEASE_REQUESTED']), name='mini_code_one_active_review'),
        ]
