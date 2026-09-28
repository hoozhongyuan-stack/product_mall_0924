"""Subscription event registration and bounded synthetic dispatch worker."""

import uuid
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from customers.models import Member
from .models import EVENT_TYPES, MessageAttempt, MessageTask, SubscriptionGrant, SubscriptionTemplate


ORDER_PAID, ORDER_SHIPPED, REFUND_SUCCEEDED = EVENT_TYPES
MAX_ATTEMPTS = 3
LEASE_DURATION = timedelta(minutes=2)
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendResult:
    outcome: str
    failure_code: str = ""

    def __post_init__(self):
        if self.outcome not in {"SIMULATED", "RETRYABLE", "PERMANENT", "UNKNOWN"}:
            raise ValueError("invalid send outcome")
        if self.outcome != "SIMULATED" and not self.failure_code:
            object.__setattr__(self, "failure_code", {
                "RETRYABLE": "RETRYABLE_FAILURE",
                "PERMANENT": "PERMANENT_FAILURE",
                "UNKNOWN": "UNKNOWN_RESULT",
            }[self.outcome])
        if not re.fullmatch(r"[A-Z0-9_]{0,32}", self.failure_code):
            raise ValueError("invalid failure code")


def _source_uuid(source_id):
    try:
        return uuid.UUID(str(source_id))
    except (AttributeError, ValueError, TypeError) as exc:
        raise ValueError("source_id must be a UUID") from exc


def member_identity_digest(member):
    """Bind a grant and task to the exact app/openid pair without copying it."""
    return hashlib.sha256(f"{member.wechat_app_id}\0{member.wechat_openid}".encode()).hexdigest()


def record_grant(*, observation_id, member_id, event_type, template_id, state,
                 observed_at, expires_at):
    """Persist one synthetic authorization observation by caller-supplied identity.

    The real Mini Program result capture and trust boundary belongs to E2.1.
    """
    observation_id = _source_uuid(observation_id)
    if event_type not in EVENT_TYPES or state not in {"ACCEPTED", "REJECTED"}:
        raise ValueError("invalid authorization observation")
    if not isinstance(template_id, str) or not template_id.strip() or len(template_id) > 128:
        raise ValueError("invalid template binding")
    if (not isinstance(observed_at, datetime) or not timezone.is_aware(observed_at) or
            not isinstance(expires_at, datetime) or not timezone.is_aware(expires_at) or
            expires_at <= observed_at):
        raise ValueError("invalid authorization observation time")
    with transaction.atomic():
        member = Member.objects.select_for_update().get(pk=member_id)
        if not member.wechat_app_id or not member.wechat_openid:
            raise ValueError("member has no WeChat identity")
        digest = member_identity_digest(member)
        fields = {"member": member, "event_type": event_type, "template_id": template_id,
                  "state": state, "evidence_source": "SYNTHETIC_TEST",
                  "identity_digest": digest, "observed_at": observed_at, "expires_at": expires_at}
        grant, created = SubscriptionGrant.objects.get_or_create(observation_id=observation_id,
                                                                  defaults=fields)
        if not created and any(getattr(grant, key) != value for key, value in fields.items()):
            raise ValueError("authorization observation conflict")
        return grant


def record_event(event_type, source_id, member_id, *, occurred_at):
    """Register a source fact in its business transaction, never send inline.

    Missing policy or authorization is a terminal decision. Enabling a template
    later cannot silently send old events. Replays return the original task.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError("unsupported subscription event")
    source_id = _source_uuid(source_id)
    if not isinstance(occurred_at, datetime) or not timezone.is_aware(occurred_at):
        raise ValueError("occurred_at must be timezone-aware")
    if not connection.in_atomic_block:
        raise RuntimeError("event registration requires surrounding business transaction")
    with transaction.atomic():
        member = Member.objects.select_for_update().get(pk=member_id)
        task, created = MessageTask.objects.get_or_create(
            event_type=event_type, source_id=source_id,
            defaults={"member": member, "occurred_at": occurred_at, "status": "BLOCKED",
                      "reason_code": "NO_TEMPLATE"})
        if not created:
            if task.member_id != member.id:
                raise ValueError("source fact belongs to another member")
            return task
        if not member.enabled:
            task.reason_code = "MEMBER_DISABLED"
        elif not member.wechat_app_id or not member.wechat_openid:
            task.reason_code = "MEMBER_IDENTITY_MISSING"
        else:
            task.identity_digest = member_identity_digest(member)
            binding = SubscriptionTemplate.objects.filter(event_type=event_type,
                                                          wechat_app_id=member.wechat_app_id).first()
            if binding is None:
                task.reason_code = "NO_TEMPLATE"
            elif not binding.enabled or not binding.template_id.strip():
                task.reason_code = "TEMPLATE_DISABLED"
            else:
                task.template_id_snapshot = binding.template_id
                observations = (SubscriptionGrant.objects.select_for_update()
                                .filter(member=member, event_type=event_type,
                                        identity_digest=task.identity_digest,
                                        observed_at__lte=occurred_at)
                                .order_by("-observed_at", "-created_at", "-id"))
                latest = observations.first()
                if latest is None:
                    task.reason_code = "NO_AUTHORIZATION"
                elif latest.template_id != binding.template_id:
                    task.reason_code = "TEMPLATE_MISMATCH"
                elif latest.state == "REJECTED":
                    task.reason_code = "AUTHORIZATION_REJECTED"
                elif latest.revoked_at is not None:
                    task.reason_code = "AUTHORIZATION_REVOKED"
                elif latest.evidence_source != "SYNTHETIC_TEST":
                    task.reason_code = "AUTHORIZATION_UNVERIFIED"
                else:
                    grant = observations.filter(state="ACCEPTED", template_id=binding.template_id,
                                                evidence_source="SYNTHETIC_TEST", revoked_at__isnull=True,
                                                expires_at__gt=occurred_at,
                                                consumed_by__isnull=True)
                    latest_rejection = observations.filter(state="REJECTED").first()
                    if latest_rejection is not None:
                        grant = grant.filter(observed_at__gt=latest_rejection.observed_at)
                    grant = grant.first()
                    if grant is None:
                        task.reason_code = "NO_AUTHORIZATION"
                    else:
                        task.status = "READY"
                        task.reason_code = ""
                        grant.consumed_by = task
                        grant.save(update_fields=["consumed_by"])
        task.save(update_fields=["status", "reason_code", "identity_digest",
                                 "template_id_snapshot", "updated_at"])
        return task


def _check_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")


def claim_next(*, now=None):
    """Claim one due task. The transaction commits before the sender is called."""
    if connection.in_atomic_block:
        raise RuntimeError("message claim requires no surrounding transaction")
    now = now or timezone.now()
    with transaction.atomic():
        query = MessageTask.objects.select_for_update(skip_locked=True).filter(
            Q(status="READY") | Q(status="RETRY_WAIT", next_attempt_at__lte=now),
            authorization_grant__evidence_source="SYNTHETIC_TEST")
        task = query.order_by("created_at", "id").first()
        if task is None:
            return None
        token = uuid.uuid4()
        task.status = "RESERVED"
        task.reason_code = ""
        task.next_attempt_at = None
        task.lease_token = token
        task.lease_until = now + LEASE_DURATION
        task.save(update_fields=["status", "reason_code", "next_attempt_at",
                                 "lease_token", "lease_until", "updated_at"])
        return task


def begin_dispatch(task_id, lease_token, *, now=None):
    """Fence a reservation and persist the attempt before any possible I/O."""
    if connection.in_atomic_block:
        raise RuntimeError("message dispatch requires no surrounding transaction")
    now = now or timezone.now()
    with transaction.atomic():
        task = MessageTask.objects.select_for_update().select_related("member").get(pk=task_id)
        if (task.status != "RESERVED" or task.lease_token != lease_token or
                task.lease_until is None or task.lease_until <= now):
            return False
        member = task.member
        grant = SubscriptionGrant.objects.filter(consumed_by=task).first()
        latest_grant = (SubscriptionGrant.objects.filter(
            member=member, event_type=task.event_type, identity_digest=task.identity_digest,
            observed_at__lte=now)
            .order_by("-observed_at", "-created_at", "-id").first())
        rejection_since_grant = (grant is not None and SubscriptionGrant.objects.filter(
            member=member, event_type=task.event_type, identity_digest=task.identity_digest,
            state="REJECTED", observed_at__gte=grant.observed_at, observed_at__lte=now).exists())
        binding = SubscriptionTemplate.objects.filter(event_type=task.event_type,
                                                       wechat_app_id=member.wechat_app_id).first()
        reason = ""
        if not member.enabled:
            reason = "MEMBER_DISABLED"
        elif not member.wechat_app_id or not member.wechat_openid:
            reason = "MEMBER_IDENTITY_MISSING"
        elif task.identity_digest != member_identity_digest(member):
            reason = "MEMBER_IDENTITY_CHANGED"
        elif binding is None or not binding.enabled or not binding.template_id.strip() or binding.template_id != task.template_id_snapshot:
            reason = "TEMPLATE_UNAVAILABLE"
        elif (grant is None or grant.state != "ACCEPTED" or grant.revoked_at is not None or
              grant.identity_digest != task.identity_digest or grant.evidence_source != "SYNTHETIC_TEST"):
            reason = "AUTHORIZATION_REVOKED"
        elif (grant.member_id != task.member_id or grant.event_type != task.event_type or
              grant.template_id != task.template_id_snapshot):
            reason = "AUTHORIZATION_MISMATCH"
        elif grant.expires_at <= now:
            reason = "AUTHORIZATION_EXPIRED"
        elif rejection_since_grant:
            reason = "AUTHORIZATION_REJECTED"
        elif latest_grant is None:
            reason = "AUTHORIZATION_UNAVAILABLE"
        elif latest_grant.state == "REJECTED":
            reason = "AUTHORIZATION_REJECTED"
        elif latest_grant.revoked_at is not None:
            reason = "AUTHORIZATION_REVOKED"
        elif latest_grant.template_id != task.template_id_snapshot:
            reason = "TEMPLATE_MISMATCH"
        elif latest_grant.evidence_source != "SYNTHETIC_TEST":
            reason = "AUTHORIZATION_UNVERIFIED"
        if reason:
            task.status = "BLOCKED"
            task.reason_code = reason
            task.lease_token = None
            task.lease_until = None
            task.save(update_fields=["status", "reason_code", "lease_token", "lease_until", "updated_at"])
            return False
        task.status = "CLAIMED"
        task.attempt_count += 1
        task.save(update_fields=["status", "attempt_count", "updated_at"])
        MessageAttempt.objects.create(task=task, ordinal=task.attempt_count, lease_token=lease_token,
                                      started_at=now)
        return True


def _finish(task_id, lease_token, result, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        task = MessageTask.objects.select_for_update().get(pk=task_id)
        if task.status != "CLAIMED" or task.lease_token != lease_token:
            return False  # Recovery fenced this worker; never overwrite UNKNOWN.
        attempt = MessageAttempt.objects.get(task=task, lease_token=lease_token)
        attempt.outcome = result.outcome
        attempt.failure_code = result.failure_code
        attempt.finished_at = now
        attempt.save(update_fields=["outcome", "failure_code", "finished_at"])
        task.reason_code = result.failure_code
        if result.outcome == "RETRYABLE" and task.attempt_count < MAX_ATTEMPTS:
            task.status = "RETRY_WAIT"
            task.next_attempt_at = now + RETRY_DELAYS[task.attempt_count - 1]
        elif result.outcome == "RETRYABLE":
            task.status = "FAILED"
            task.reason_code = result.failure_code or "RETRY_EXHAUSTED"
        elif result.outcome == "PERMANENT":
            task.status = "FAILED"
        else:
            task.status = result.outcome
        task.lease_token = None
        task.lease_until = None
        task.save(update_fields=["status", "reason_code", "next_attempt_at", "lease_token",
                                 "lease_until", "updated_at"])
        return True


def recover_expired_claims(*, now=None, limit=100):
    """A crashed worker's possible send is UNKNOWN, never an automatic retry."""
    _check_limit(limit)
    now = now or timezone.now()
    with transaction.atomic():
        tasks = list(MessageTask.objects.select_for_update(skip_locked=True)
                     .filter(status__in=["RESERVED", "CLAIMED"], lease_until__lte=now)
                     .order_by("lease_until", "id")[:limit])
        for task in tasks:
            if task.status == "CLAIMED":
                MessageAttempt.objects.filter(task=task, lease_token=task.lease_token,
                                              outcome="IN_FLIGHT").update(
                    outcome="UNKNOWN", failure_code="LEASE_EXPIRED", finished_at=now)
                task.status = "UNKNOWN"
                task.reason_code = "LEASE_EXPIRED"
            else:
                task.status = "READY"
                task.reason_code = ""
            task.lease_token = None
            task.lease_until = None
            task.save(update_fields=["status", "reason_code", "lease_token", "lease_until", "updated_at"])
        return len(tasks)


def process_tasks(*, limit, sender):
    """Run one bounded tick. Sender executes after claim transaction commits."""
    _check_limit(limit)
    if not callable(sender):
        raise ValueError("sender must be callable")
    if connection.in_atomic_block:
        raise RuntimeError("message worker requires no surrounding transaction")
    processed = 0
    for _ in range(limit):
        task = claim_next()
        if task is None:
            break
        if not begin_dispatch(task.pk, task.lease_token):
            continue
        try:
            result = sender(task)
            if not isinstance(result, SendResult):
                raise TypeError("sender must return SendResult")
        except Exception as exc:
            logger.warning("subscription_sender_unknown task_id=%s error_type=%s", task.pk,
                           type(exc).__name__)
            result = SendResult("UNKNOWN", "SENDER_EXCEPTION")
        _finish(task.pk, task.lease_token, result)
        processed += 1
    return processed
