import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class MicroPage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page_type = models.CharField(max_length=5, default="HOME")
    name = models.CharField(max_length=80, default="首页")
    draft_revision = models.PositiveIntegerField(default=1)
    draft_config = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "micro_page"
        constraints = [
            models.UniqueConstraint(fields=["page_type"], condition=Q(page_type="HOME"),
                                    name="one_home_page"),
            models.CheckConstraint(condition=Q(page_type__in=["HOME", "MICRO"]),
                                   name="known_page_type"),
        ]


class PageConfigVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(MicroPage, on_delete=models.PROTECT, related_name="versions")
    revision = models.PositiveIntegerField()
    name = models.CharField(max_length=80, default="")
    config_json = models.JSONField()
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "page_config_version"
        constraints = [models.UniqueConstraint(fields=["page", "revision"], name="unique_page_revision")]


class PagePublication(models.Model):
    page = models.OneToOneField(MicroPage, on_delete=models.PROTECT, primary_key=True)
    current_version = models.ForeignKey(PageConfigVersion, null=True, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "page_publication"


class PageDraftAsset(models.Model):
    page = models.ForeignKey(MicroPage, on_delete=models.CASCADE)
    asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT)

    class Meta:
        db_table = "page_draft_asset"
        constraints = [models.UniqueConstraint(fields=["page", "asset"], name="unique_page_draft_asset")]


class PageVersionAsset(models.Model):
    version = models.ForeignKey(PageConfigVersion, on_delete=models.PROTECT)
    asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT)
    is_public = models.BooleanField(default=False)

    class Meta:
        db_table = "page_version_asset"
        constraints = [models.UniqueConstraint(fields=["version", "asset"], name="unique_page_version_asset")]


class PagePublishRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(MicroPage, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    version = models.ForeignKey(PageConfigVersion, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "page_publish_request"
        constraints = [models.UniqueConstraint(fields=["actor", "page", "key"], name="unique_page_publish_key")]


class StartupConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    draft_revision = models.PositiveIntegerField(default=1)
    gif_asset = models.ForeignKey("catalog.Asset", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="startup_gif_drafts")
    fallback_asset = models.ForeignKey("catalog.Asset", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="startup_fallback_drafts")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "startup_config"
        constraints = [models.CheckConstraint(condition=Q(id=1), name="one_startup_config")]


class StartupConfigVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.PositiveIntegerField()
    gif_asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT,
                                  related_name="startup_gif_versions")
    fallback_asset = models.ForeignKey("catalog.Asset", on_delete=models.PROTECT,
                                       related_name="startup_fallback_versions")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "startup_config_version"
        constraints = [models.UniqueConstraint(fields=["revision"], name="unique_startup_revision")]


class StartupPublication(models.Model):
    config = models.OneToOneField(StartupConfig, on_delete=models.PROTECT, primary_key=True)
    current_version = models.ForeignKey(StartupConfigVersion, null=True, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "startup_publication"


class StartupPublishRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    version = models.ForeignKey(StartupConfigVersion, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "startup_publish_request"
        constraints = [models.UniqueConstraint(fields=["actor", "key"], name="unique_startup_publish_key")]


class ContentRollbackRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    domain = models.CharField(max_length=7)
    object_id = models.CharField(max_length=36)
    key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "content_rollback_request"
        constraints = [models.UniqueConstraint(fields=["actor", "domain", "object_id", "key"],
                                               name="unique_content_rollback_key")]
