from django.conf import settings
from django.db import models


class ComponentConfig(models.Model):
    """One platform identity; all credentials and tickets are encrypted."""

    component_app_id = models.CharField(max_length=64, blank=True)
    developer_app_id = models.CharField(max_length=64, blank=True)
    redirect_uri = models.URLField(max_length=512, blank=True)
    encrypted_credentials = models.TextField(blank=True)
    encrypted_ticket = models.TextField(blank=True)
    ticket_received_at = models.DateTimeField(null=True, blank=True)
    ticket_event_time = models.PositiveBigIntegerField(default=0)
    encrypted_access_token = models.TextField(blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class AuthorizerGrant(models.Model):
    component_app_id = models.CharField(max_length=64)
    authorizer_app_id = models.CharField(max_length=64, unique=True)
    encrypted_refresh_token = models.TextField(blank=True)
    encrypted_access_token = models.TextField(blank=True)
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    scope_ids = models.JSONField(default=list)
    revoked_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    account_status = models.IntegerField(null=True, blank=True)
    last_event_time = models.PositiveBigIntegerField(default=0)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class AuthorizationIntent(models.Model):
    state_hash = models.CharField(max_length=64, unique=True)
    target_app_id = models.CharField(max_length=64)
    component_revision = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
