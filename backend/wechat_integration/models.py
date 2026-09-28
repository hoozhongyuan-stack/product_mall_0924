"""One encrypted application identity, independent of payment/channel switches."""
from django.conf import settings
from django.db import models


class MiniProgramIntegration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    managed = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    encrypted_payload = models.TextField(default='', blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
    last_check_revision = models.PositiveIntegerField(null=True)
    last_check_status = models.CharField(max_length=16, default='', blank=True)
    last_check_code = models.CharField(max_length=32, default='', blank=True)
    last_check_fingerprint = models.CharField(max_length=64, default='', blank=True)
    last_check_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name='wechat_integration_singleton')]
