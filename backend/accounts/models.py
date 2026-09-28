import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q


class AdminAccountManager(BaseUserManager):
    def create_user(self, login_name, password, **fields):
        if not login_name:
            raise ValueError("login_name is required")
        account = self.model(login_name=login_name.strip().lower(), **fields)
        account.set_password(password)
        account.save(using=self._db)
        return account


class AdminAccount(AbstractBaseUser):
    class Kind(models.TextChoices):
        OWNER = "OWNER", "Owner"
        STAFF = "STAFF", "Staff"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    password = models.CharField(max_length=128, db_column="password_hash")
    login_name = models.CharField(max_length=150, unique=True)
    display_name = models.CharField(max_length=120)
    kind = models.CharField(max_length=5, choices=Kind.choices, default=Kind.STAFF)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    auth_version = models.PositiveIntegerField(default=1)
    failed_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_active_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    permission_groups = models.ManyToManyField("PermissionGroup", through="AccountGroup", related_name="accounts")

    USERNAME_FIELD = "login_name"
    objects = AdminAccountManager()

    class Meta:
        db_table = "admin_account"
        constraints = [models.UniqueConstraint(fields=["kind"], condition=Q(kind="OWNER"), name="one_admin_owner")]

    @property
    def is_active(self):
        return self.enabled


class PermissionGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=60, unique=True)
    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "permission_group"


class AccountGroup(models.Model):
    account = models.ForeignKey(AdminAccount, on_delete=models.CASCADE)
    group = models.ForeignKey(PermissionGroup, on_delete=models.PROTECT)

    class Meta:
        db_table = "account_group"
        constraints = [models.UniqueConstraint(fields=["account", "group"], name="unique_account_group")]


class GroupPermission(models.Model):
    group = models.ForeignKey(PermissionGroup, on_delete=models.CASCADE, related_name="permissions")
    code = models.CharField(max_length=80)

    class Meta:
        db_table = "group_permission"
        constraints = [models.UniqueConstraint(fields=["group", "code"], name="unique_group_permission")]


class AuditLog(models.Model):
    class Result(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        DENIED = "DENIED", "Denied"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(AdminAccount, null=True, blank=True, on_delete=models.SET_NULL)
    action_code = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=100, blank=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    result = models.CharField(max_length=7, choices=Result.choices)
    request_id = models.UUIDField()
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_log"
        ordering = ["-occurred_at"]


class ActionConfirmation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    actor = models.ForeignKey(AdminAccount, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=40)
    action = models.CharField(max_length=80)
    object_id = models.CharField(max_length=100, blank=True)
    revision = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "action_confirmation"
