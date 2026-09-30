import hashlib
import secrets
import uuid
from datetime import timedelta

from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone


from .profile import member_number


class Member(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member_no = models.CharField(max_length=18, default=member_number, editable=False)
    nickname = models.CharField(max_length=40, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    profile_revision = models.PositiveIntegerField(default=1)
    avatar_id = models.UUIDField(null=True, unique=True, editable=False)
    avatar_path = models.CharField(max_length=200, blank=True, default="", editable=False)
    avatar_content_type = models.CharField(max_length=30, blank=True, default="", editable=False)
    wechat_app_id = models.CharField(max_length=64)
    wechat_openid = models.CharField(max_length=128)
    grade = models.ForeignKey("catalog.MemberGrade", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)
    auth_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            return super().save(*args, **kwargs)
        for attempt in range(10):
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:
                constraint = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", "")
                if constraint != "unique_member_number":
                    raise
                self.member_no = member_number()
        raise IntegrityError("Member number allocation exhausted")

    class Meta:
        db_table = "customer_member"
        constraints = [models.UniqueConstraint(fields=["wechat_app_id", "wechat_openid"], name="unique_wechat_member"),
                       models.UniqueConstraint(fields=["member_no"], name="unique_member_number")]
        indexes = [models.Index(fields=['-created_at', '-id'], name='member_created_id_idx'),
                   models.Index(fields=['grade', 'enabled', '-created_at'], name='member_grade_state_idx')]


class MemberProfileQuota(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE)
    scope = models.CharField(max_length=16)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "customer_profile_quota"
        constraints = [models.UniqueConstraint(fields=["member", "scope"], name="member_profile_quota_scope")]


class MemberSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="sessions")
    token_digest = models.CharField(max_length=64, unique=True)
    auth_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "customer_session"

    @classmethod
    def issue(cls, member):
        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(days=7)
        cls.objects.create(member=member, token_digest=hashlib.sha256(token.encode()).hexdigest(),
                           auth_version=member.auth_version, expires_at=expires_at)
        return token, expires_at


class WechatCodeUse(models.Model):
    code_digest = models.CharField(max_length=64, primary_key=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer_wechat_code_use"


class WechatLoginAttempt(models.Model):
    source_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer_wechat_login_attempt"
        indexes = [models.Index(fields=["source_digest", "created_at"], name="wechat_attempt_source_time")]


class CustomerAddress(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="addresses")
    recipient_name = models.CharField(max_length=40)
    phone = models.CharField(max_length=20)
    province = models.CharField(max_length=40)
    city = models.CharField(max_length=40)
    district = models.CharField(max_length=40)
    detail = models.CharField(max_length=200)
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer_address"
        constraints = [models.UniqueConstraint(fields=["member"], condition=Q(active=True, is_default=True),
                                               name="unique_active_default_address")]
        indexes = [models.Index(fields=["member", "active", "-updated_at"], name="address_member_active_time")]


class GradeThreshold(models.Model):
    grade = models.OneToOneField('catalog.MemberGrade', primary_key=True, on_delete=models.PROTECT)
    minimum_spend_fen = models.PositiveBigIntegerField(default=0)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = 'customer_grade_threshold'


class MemberConsumption(models.Model):
    member = models.OneToOneField(Member, primary_key=True, on_delete=models.PROTECT)
    effective_spend_fen = models.PositiveBigIntegerField(default=0)
    grade_effective_at = models.DateTimeField(null=True)
    grade_policy_revision = models.PositiveIntegerField(default=0)
    grade_thresholds = models.JSONField(default=list)

    class Meta:
        db_table = 'customer_member_consumption'


class ConsumptionOrder(models.Model):
    order_id = models.UUIDField(primary_key=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT)
    effective_spend_fen = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = 'customer_consumption_order'


class ConsumptionEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey(Member, on_delete=models.PROTECT)
    order_id = models.UUIDField()
    amount_fen = models.BigIntegerField()
    balance_fen = models.PositiveBigIntegerField()
    grade_id_before = models.UUIDField()
    grade_id_after = models.UUIDField()
    source_ref = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'customer_consumption_event'
        indexes = [models.Index(fields=['member', '-created_at'], name='consume_member_time_idx')]


class MemberRuleChange(models.Model):
    """Immutable rule revision and actor request identity; no account secrets."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey('accounts.AdminAccount', on_delete=models.PROTECT)
    request_key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    revision = models.PositiveIntegerField(unique=True)
    before = models.JSONField()
    after = models.JSONField()
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'customer_member_rule_change'
        constraints = [models.UniqueConstraint(fields=['actor','request_key'], name='member_rule_actor_key_unique')]
