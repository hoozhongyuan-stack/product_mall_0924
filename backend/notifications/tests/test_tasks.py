import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from catalog.models import MemberGrade
from customers.models import Member
from notifications.models import MessageAttempt, MessageTask, SubscriptionGrant, SubscriptionTemplate
from notifications.service import (ORDER_PAID, ORDER_SHIPPED, SendResult, process_tasks,
                                   member_identity_digest, record_event as service_record_event,
                                   recover_expired_claims)


def record_event(*args, **kwargs):
    """Exercise the public entrance inside the caller's business transaction."""
    with transaction.atomic():
        return service_record_event(*args, **kwargs)


class MessageTaskTests(TransactionTestCase):
    def setUp(self):
        grade = MemberGrade.objects.create(code="msg-grade", name="消息会员", rank=98)
        self.member = Member.objects.create(wechat_app_id="wx-message-test", wechat_openid="private-openid",
                                            grade=grade)
        self.now = timezone.now()

    def _ready(self, event=ORDER_PAID):
        SubscriptionTemplate.objects.create(event_type=event, wechat_app_id=self.member.wechat_app_id,
                                            template_id="template-test-1", enabled=True)
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                         event_type=event, template_id="template-test-1",
                                         state="ACCEPTED", evidence_source="SYNTHETIC_TEST",
                                         identity_digest=member_identity_digest(self.member),
                                         observed_at=self.now, expires_at=self.now + timedelta(hours=1))
        return record_event(event, uuid.uuid4(), self.member.id, occurred_at=self.now)

    def test_default_closed_persists_blocked_task_and_deduplicates(self):
        source_id = uuid.uuid4()
        first = record_event(ORDER_PAID, source_id, self.member.id, occurred_at=self.now)
        second = record_event(ORDER_PAID, source_id, self.member.id, occurred_at=self.now)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, "BLOCKED")
        self.assertEqual(first.reason_code, "NO_TEMPLATE")
        self.assertEqual(MessageTask.objects.count(), 1)
        self.assertEqual(process_tasks(limit=10, sender=lambda task: SendResult("SIMULATED")), 0)

    def test_disabled_template_and_missing_consent_do_not_send(self):
        template = SubscriptionTemplate.objects.create(event_type=ORDER_PAID,
                                                       wechat_app_id=self.member.wechat_app_id,
                                                       template_id="template-test-1")
        disabled = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        template.enabled = True
        template.save(update_fields=["enabled"])
        missing = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        self.assertEqual((disabled.status, disabled.reason_code), ("BLOCKED", "TEMPLATE_DISABLED"))
        self.assertEqual((missing.status, missing.reason_code), ("BLOCKED", "NO_AUTHORIZATION"))
        self.assertEqual(process_tasks(limit=10, sender=lambda task: SendResult("SIMULATED")), 0)

    def test_grant_is_consumed_once_and_template_snapshot_is_persisted(self):
        task = self._ready()
        self.assertEqual(task.status, "READY")
        self.assertEqual(task.template_id_snapshot, "template-test-1")
        grant = SubscriptionGrant.objects.get(member=self.member)
        self.assertEqual(grant.consumed_by_id, task.id)
        next_task = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        self.assertEqual((next_task.status, next_task.reason_code), ("BLOCKED", "NO_AUTHORIZATION"))

    def test_two_accepted_grants_support_two_distinct_events(self):
        SubscriptionTemplate.objects.create(event_type=ORDER_PAID, wechat_app_id=self.member.wechat_app_id,
                                            template_id="template-test-1", enabled=True)
        older = SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                                 event_type=ORDER_PAID,
                                                 template_id="template-test-1", state="ACCEPTED",
                                                 evidence_source="SYNTHETIC_TEST",
                                                 identity_digest=member_identity_digest(self.member),
                                                 observed_at=self.now - timedelta(minutes=2),
                                                 expires_at=self.now + timedelta(hours=1))
        newer = SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                                 event_type=ORDER_PAID,
                                                 template_id="template-test-1", state="ACCEPTED",
                                                 evidence_source="SYNTHETIC_TEST",
                                                 identity_digest=member_identity_digest(self.member),
                                                 observed_at=self.now - timedelta(minutes=1),
                                                 expires_at=self.now + timedelta(hours=1))
        first = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        second = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual((first.status, second.status), ("READY", "READY"))
        self.assertEqual((newer.consumed_by_id, older.consumed_by_id), (first.id, second.id))

    def test_authorization_observation_replay_does_not_mint_extra_quota(self):
        from notifications.service import record_grant

        SubscriptionTemplate.objects.create(event_type=ORDER_PAID, wechat_app_id=self.member.wechat_app_id,
                                            template_id="template-test-1", enabled=True)
        observation_id = uuid.uuid4()
        fields = dict(observation_id=observation_id, member_id=self.member.id, event_type=ORDER_PAID,
                      template_id="template-test-1", state="ACCEPTED", observed_at=self.now,
                      expires_at=self.now + timedelta(hours=1))
        first_grant = record_grant(**fields)
        replay_grant = record_grant(**fields)
        self.assertEqual(first_grant.id, replay_grant.id)
        with self.assertRaisesMessage(ValueError, "observation conflict"):
            record_grant(**{**fields, "state": "REJECTED"})
        first_task = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        second_task = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        self.assertEqual((first_task.status, second_task.status), ("READY", "BLOCKED"))
        self.assertEqual(second_task.reason_code, "NO_AUTHORIZATION")
        self.assertEqual(SubscriptionGrant.objects.filter(observation_id=observation_id).count(), 1)

    def test_rejection_epoch_does_not_reactivate_older_unused_grant(self):
        SubscriptionTemplate.objects.create(event_type=ORDER_PAID, wechat_app_id=self.member.wechat_app_id,
                                            template_id="template-test-1", enabled=True)
        common = dict(member=self.member, event_type=ORDER_PAID, template_id="template-test-1",
                      evidence_source="SYNTHETIC_TEST",
                      identity_digest=member_identity_digest(self.member),
                      expires_at=self.now + timedelta(hours=1))
        older = SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), state="ACCEPTED",
                                                 observed_at=self.now - timedelta(minutes=3), **common)
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), state="REJECTED",
                                         observed_at=self.now - timedelta(minutes=2), **common)
        newer = SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), state="ACCEPTED",
                                                 observed_at=self.now - timedelta(minutes=1), **common)
        first = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        second = record_event(ORDER_PAID, uuid.uuid4(), self.member.id, occurred_at=self.now)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(first.status, "READY")
        self.assertEqual(newer.consumed_by_id, first.id)
        self.assertIsNone(older.consumed_by_id)
        self.assertEqual((second.status, second.reason_code), ("BLOCKED", "NO_AUTHORIZATION"))

    def test_grant_from_previous_wechat_identity_is_not_reused(self):
        SubscriptionTemplate.objects.create(event_type=ORDER_PAID, wechat_app_id=self.member.wechat_app_id,
                                            template_id="template-test-1", enabled=True)
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                         event_type=ORDER_PAID,
                                         template_id="template-test-1", state="ACCEPTED",
                                         evidence_source="SYNTHETIC_TEST",
                                         identity_digest=member_identity_digest(self.member),
                                         observed_at=self.now, expires_at=self.now + timedelta(hours=1))
        self.member.wechat_openid = "new-private-openid"
        self.member.save(update_fields=["wechat_openid"])
        task = record_event(ORDER_PAID, uuid.uuid4(), self.member.pk, occurred_at=self.now)
        self.assertEqual((task.status, task.reason_code), ("BLOCKED", "NO_AUTHORIZATION"))

    def test_explicit_rejection_and_template_mismatch_are_distinct(self):
        SubscriptionTemplate.objects.create(event_type=ORDER_PAID, wechat_app_id=self.member.wechat_app_id,
                                            template_id="active-template", enabled=True)
        grant = SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                                 event_type=ORDER_PAID,
                                                 template_id="active-template", state="REJECTED",
                                                 evidence_source="SYNTHETIC_TEST",
                                                 identity_digest=member_identity_digest(self.member),
                                                 observed_at=self.now, expires_at=self.now + timedelta(hours=1))
        rejected = record_event(ORDER_PAID, uuid.uuid4(), self.member.pk, occurred_at=self.now)
        self.assertEqual((rejected.status, rejected.reason_code), ("BLOCKED", "AUTHORIZATION_REJECTED"))
        grant.state = "ACCEPTED"
        grant.template_id = "old-template"
        grant.save(update_fields=["state", "template_id"])
        mismatch = record_event(ORDER_PAID, uuid.uuid4(), self.member.pk, occurred_at=self.now)
        self.assertEqual((mismatch.status, mismatch.reason_code), ("BLOCKED", "TEMPLATE_MISMATCH"))

    def test_duplicate_source_rolls_back_with_business_transaction(self):
        source_id = uuid.uuid4()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                record_event(ORDER_PAID, source_id, self.member.id, occurred_at=self.now)
                raise RuntimeError("rollback")
        self.assertFalse(MessageTask.objects.filter(source_id=source_id).exists())

    def test_event_registration_requires_business_transaction(self):
        source_id = uuid.uuid4()
        with self.assertRaisesMessage(RuntimeError, "business transaction"):
            service_record_event(ORDER_PAID, source_id, self.member.id, occurred_at=self.now)
        self.assertFalse(MessageTask.objects.filter(source_id=source_id).exists())

    def test_one_source_cannot_target_another_member(self):
        source_id = uuid.uuid4()
        record_event(ORDER_PAID, source_id, self.member.id, occurred_at=self.now)
        other = Member.objects.create(wechat_app_id=self.member.wechat_app_id,
                                      wechat_openid="other-private-openid", grade=self.member.grade)
        with self.assertRaisesMessage(ValueError, "another member"):
            record_event(ORDER_PAID, source_id, other.id, occurred_at=self.now)
        self.assertEqual(MessageTask.objects.count(), 1)

    def test_synthetic_result_is_never_claimed_as_real_delivery(self):
        task = self._ready()
        self.assertEqual(process_tasks(limit=10, sender=lambda item: SendResult("SIMULATED")), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "SIMULATED")
        self.assertEqual(MessageAttempt.objects.get(task=task).outcome, "SIMULATED")

    def test_retryable_failure_is_bounded_and_not_due_immediately(self):
        task = self._ready()
        self.assertEqual(process_tasks(limit=1, sender=lambda item: SendResult("RETRYABLE", "RATE_LIMIT")), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "RETRY_WAIT")
        self.assertEqual(task.attempt_count, 1)
        self.assertGreater(task.next_attempt_at, timezone.now())
        self.assertEqual(process_tasks(limit=1, sender=lambda item: SendResult("SIMULATED")), 0)

    def test_retryable_failure_stops_after_three_attempts(self):
        task = self._ready()
        for ordinal in range(3):
            self.assertEqual(process_tasks(limit=1, sender=lambda item: SendResult("RETRYABLE", "RATE_LIMIT")), 1)
            task.refresh_from_db()
            if ordinal < 2:
                self.assertEqual(task.status, "RETRY_WAIT")
                MessageTask.objects.filter(pk=task.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        task.refresh_from_db()
        self.assertEqual((task.status, task.attempt_count, task.reason_code), ("FAILED", 3, "RATE_LIMIT"))
        self.assertIsNone(task.next_attempt_at)
        self.assertEqual(list(task.attempts.order_by("ordinal").values_list("outcome", flat=True)),
                         ["RETRYABLE", "RETRYABLE", "RETRYABLE"])
        self.assertEqual(process_tasks(limit=1, sender=lambda item: self.fail("unexpected fourth send")), 0)

    def test_exception_becomes_unknown_and_is_not_blindly_retried(self):
        task = self._ready()

        def failed(_):
            raise TimeoutError("potentially sent")

        self.assertEqual(process_tasks(limit=1, sender=failed), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "UNKNOWN")
        self.assertEqual(process_tasks(limit=1, sender=lambda item: SendResult("SIMULATED")), 0)
        self.assertEqual(MessageAttempt.objects.get(task=task).outcome, "UNKNOWN")

    def test_non_success_outcomes_always_have_reason_codes(self):
        self.assertEqual(SendResult("RETRYABLE").failure_code, "RETRYABLE_FAILURE")
        self.assertEqual(SendResult("PERMANENT").failure_code, "PERMANENT_FAILURE")
        self.assertEqual(SendResult("UNKNOWN").failure_code, "UNKNOWN_RESULT")
        task = self._ready()
        self.assertEqual(process_tasks(limit=1, sender=lambda item: SendResult("PERMANENT")), 1)
        task.refresh_from_db()
        self.assertEqual((task.status, task.reason_code), ("FAILED", "PERMANENT_FAILURE"))
        attempt = MessageAttempt.objects.get(task=task)
        self.assertEqual((attempt.outcome, attempt.failure_code), ("PERMANENT", "PERMANENT_FAILURE"))

    def test_worker_rejects_surrounding_transaction_before_claim(self):
        task = self._ready()
        with transaction.atomic():
            with self.assertRaisesMessage(RuntimeError, "no surrounding transaction"):
                process_tasks(limit=1, sender=lambda item: self.fail("unexpected send"))
        task.refresh_from_db()
        self.assertEqual(task.status, "READY")
        self.assertFalse(MessageAttempt.objects.filter(task=task).exists())

    def test_expired_reservation_recovers_to_ready_without_attempt(self):
        task = self._ready()
        from notifications.service import claim_next

        claimed = claim_next(now=self.now)
        self.assertEqual(claimed.pk, task.pk)
        self.assertEqual(recover_expired_claims(now=self.now + timedelta(minutes=5), limit=10), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "READY")
        self.assertEqual(task.attempt_count, 0)
        self.assertFalse(MessageAttempt.objects.filter(task=task).exists())
        self.assertIsNone(task.lease_token)

    def test_expired_reservation_cannot_begin_dispatch_before_recovery(self):
        task = self._ready()
        from notifications.service import begin_dispatch, claim_next

        claimed = claim_next(now=self.now)
        late = self.now + timedelta(minutes=3)
        self.assertFalse(begin_dispatch(claimed.pk, claimed.lease_token, now=late))
        self.assertEqual(recover_expired_claims(now=late, limit=1), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "READY")
        self.assertFalse(MessageAttempt.objects.filter(task=task).exists())

    def test_expired_inflight_lease_recovers_to_unknown(self):
        task = self._ready()
        from notifications.service import begin_dispatch, claim_next

        claimed = claim_next(now=self.now)
        self.assertTrue(begin_dispatch(claimed.pk, claimed.lease_token, now=self.now))
        self.assertEqual(recover_expired_claims(now=self.now + timedelta(minutes=5), limit=10), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "UNKNOWN")
        self.assertIsNone(task.lease_token)
        self.assertEqual(MessageAttempt.objects.get(task=task).outcome, "UNKNOWN")

    def test_dispatch_rechecks_revoked_authorization(self):
        task = self._ready()
        grant = SubscriptionGrant.objects.get(consumed_by=task)
        grant.revoked_at = self.now + timedelta(seconds=1)
        grant.save(update_fields=["revoked_at"])
        self.assertEqual(process_tasks(limit=1, sender=lambda item: self.fail("unexpected send")), 0)
        task.refresh_from_db()
        self.assertEqual((task.status, task.reason_code), ("BLOCKED", "AUTHORIZATION_REVOKED"))

    def test_later_rejection_blocks_previously_ready_task(self):
        task = self._ready()
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                         event_type=ORDER_PAID,
                                         template_id="template-test-1", state="REJECTED",
                                         evidence_source="SYNTHETIC_TEST",
                                         identity_digest=member_identity_digest(self.member),
                                         observed_at=self.now + timedelta(seconds=1),
                                         expires_at=self.now + timedelta(hours=1))
        from notifications.service import begin_dispatch, claim_next

        claimed = claim_next(now=self.now + timedelta(seconds=2))
        self.assertFalse(begin_dispatch(claimed.pk, claimed.lease_token,
                                        now=self.now + timedelta(seconds=2)))
        task.refresh_from_db()
        self.assertEqual((task.status, task.reason_code), ("BLOCKED", "AUTHORIZATION_REJECTED"))

    def test_expired_rejection_still_blocks_older_allocated_grant(self):
        task = self._ready()
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                         event_type=ORDER_PAID, template_id="template-test-1",
                                         state="REJECTED", evidence_source="SYNTHETIC_TEST",
                                         identity_digest=member_identity_digest(self.member),
                                         observed_at=self.now + timedelta(seconds=1),
                                         expires_at=self.now + timedelta(seconds=2))
        from notifications.service import begin_dispatch, claim_next

        late = self.now + timedelta(seconds=3)
        claimed = claim_next(now=late)
        self.assertFalse(begin_dispatch(claimed.pk, claimed.lease_token, now=late))
        task.refresh_from_db()
        self.assertEqual((task.status, task.reason_code), ("BLOCKED", "AUTHORIZATION_REJECTED"))

    def test_later_acceptance_does_not_block_allocated_grant(self):
        task = self._ready()
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                         event_type=ORDER_PAID,
                                         template_id="template-test-1", state="ACCEPTED",
                                         evidence_source="SYNTHETIC_TEST",
                                         identity_digest=member_identity_digest(self.member),
                                         observed_at=self.now + timedelta(seconds=1),
                                         expires_at=self.now + timedelta(hours=1))
        from notifications.service import begin_dispatch, claim_next

        claimed = claim_next(now=self.now + timedelta(seconds=2))
        self.assertTrue(begin_dispatch(claimed.pk, claimed.lease_token,
                                       now=self.now + timedelta(seconds=2)))

    def test_dispatch_rechecks_disabled_template(self):
        task = self._ready()
        binding = SubscriptionTemplate.objects.get(event_type=ORDER_PAID)
        binding.enabled = False
        binding.save(update_fields=["enabled"])
        self.assertEqual(process_tasks(limit=1, sender=lambda item: self.fail("unexpected send")), 0)
        task.refresh_from_db()
        self.assertEqual((task.status, task.reason_code), ("BLOCKED", "TEMPLATE_UNAVAILABLE"))

    def test_dispatch_blocks_grant_template_mutation(self):
        task = self._ready()
        grant = SubscriptionGrant.objects.get(consumed_by=task)
        grant.template_id = "other-template"
        grant.save(update_fields=["template_id"])
        self.assertEqual(process_tasks(limit=1, sender=lambda item: self.fail("unexpected send")), 0)
        task.refresh_from_db()
        self.assertEqual((task.status, task.reason_code), ("BLOCKED", "AUTHORIZATION_MISMATCH"))

    def test_dispatch_blocks_member_identity_change(self):
        task = self._ready()
        original_digest = task.identity_digest
        self.assertEqual(len(original_digest), 64)
        self.assertNotIn(self.member.wechat_openid, original_digest)
        self.member.wechat_openid = "a-different-openid"
        self.member.save(update_fields=["wechat_openid"])
        self.assertEqual(process_tasks(limit=1, sender=lambda item: self.fail("unexpected send")), 0)
        task.refresh_from_db()
        self.assertEqual((task.status, task.reason_code), ("BLOCKED", "MEMBER_IDENTITY_CHANGED"))

    def test_recover_fences_late_sender_result(self):
        task = self._ready()
        from notifications.service import _finish, begin_dispatch, claim_next

        claimed = claim_next(now=self.now)
        self.assertTrue(begin_dispatch(claimed.pk, claimed.lease_token, now=self.now))
        recover_expired_claims(now=self.now + timedelta(minutes=5), limit=1)
        self.assertFalse(_finish(claimed.pk, claimed.lease_token, SendResult("SIMULATED")))
        task.refresh_from_db()
        self.assertEqual(task.status, "UNKNOWN")

    def test_finalize_database_failure_keeps_claim_for_unknown_recovery(self):
        task = self._ready()
        with patch("notifications.service._finish", side_effect=DatabaseError("database write failed")):
            with self.assertRaises(DatabaseError):
                process_tasks(limit=1, sender=lambda item: SendResult("SIMULATED"))
        task.refresh_from_db()
        self.assertEqual(task.status, "CLAIMED")
        self.assertEqual(recover_expired_claims(now=timezone.now() + timedelta(minutes=5), limit=1), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "UNKNOWN")
        self.assertEqual(task.attempts.get().outcome, "UNKNOWN")

    def test_rejects_unrecognized_event_and_invalid_limit(self):
        with self.assertRaises(ValueError):
            record_event("ARBITRARY", uuid.uuid4(), self.member.id, occurred_at=self.now)
        with self.assertRaises(ValueError):
            process_tasks(limit=0, sender=lambda item: SendResult("SIMULATED"))
        with self.assertRaises(ValueError):
            process_tasks(limit=1, sender=None)

    def test_command_defaults_to_recovery_only_and_synthetic_is_explicit(self):
        task = self._ready()
        output = StringIO()
        call_command("run_subscription_jobs", stdout=output)
        task.refresh_from_db()
        self.assertEqual(task.status, "READY")
        self.assertIn("synthetic_processed=0", output.getvalue())
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("run_subscription_jobs", synthetic=True)
        with override_settings(DEBUG=True):
            call_command("run_subscription_jobs", synthetic=True, stdout=StringIO())
        task.refresh_from_db()
        self.assertEqual(task.status, "SIMULATED")


class MessageDispatchTransactionTests(TransactionTestCase):
    def setUp(self):
        grade = MemberGrade.objects.create(code="msg-concurrency-grade", name="并发消息会员", rank=97)
        self.member = Member.objects.create(wechat_app_id="wx-message-concurrent",
                                            wechat_openid="private-concurrent-openid", grade=grade)
        self.now = timezone.now()
        SubscriptionTemplate.objects.create(event_type=ORDER_SHIPPED, wechat_app_id=self.member.wechat_app_id,
                                            template_id="template-concurrent", enabled=True)
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=self.member,
                                         event_type=ORDER_SHIPPED,
                                         template_id="template-concurrent", state="ACCEPTED",
                                         evidence_source="SYNTHETIC_TEST",
                                         identity_digest=member_identity_digest(self.member),
                                         observed_at=self.now,
                                         expires_at=self.now + timedelta(hours=1))
        self.task = record_event(ORDER_SHIPPED, uuid.uuid4(), self.member.pk, occurred_at=self.now)

    def test_sender_runs_outside_database_transaction(self):
        observed = []

        def sender(_):
            observed.append(connection.in_atomic_block)
            return SendResult("SIMULATED")

        self.assertEqual(process_tasks(limit=1, sender=sender), 1)
        self.assertEqual(observed, [False])

    def test_parallel_claims_only_reserve_once(self):
        from notifications.service import claim_next

        barrier = Barrier(2)

        def claim():
            try:
                barrier.wait(timeout=5)
                task = claim_next()
                return task.pk if task else None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(results.count(self.task.pk), 1)
        self.assertEqual(results.count(None), 1)


class AuthorizationCapacityConcurrencyTests(TransactionTestCase):
    def test_two_source_events_contend_for_one_grant(self):
        grade = MemberGrade.objects.create(code="msg-grant-race", name="并发授权会员", rank=96)
        member = Member.objects.create(wechat_app_id="wx-grant-race", wechat_openid="private-grant-race",
                                       grade=grade)
        now = timezone.now()
        SubscriptionTemplate.objects.create(event_type=ORDER_PAID, wechat_app_id=member.wechat_app_id,
                                            template_id="race-template", enabled=True)
        SubscriptionGrant.objects.create(observation_id=uuid.uuid4(), member=member,
                                         event_type=ORDER_PAID, template_id="race-template",
                                         state="ACCEPTED", evidence_source="SYNTHETIC_TEST",
                                         identity_digest=member_identity_digest(member),
                                         observed_at=now, expires_at=now + timedelta(hours=1))
        barrier = Barrier(2)

        def register(_):
            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    return service_record_event(ORDER_PAID, uuid.uuid4(), member.pk,
                                                occurred_at=now).status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(register, range(2)))
        self.assertCountEqual(statuses, ["READY", "BLOCKED"])
        self.assertEqual(MessageTask.objects.count(), 2)
        self.assertEqual(SubscriptionGrant.objects.filter(consumed_by__isnull=False).count(), 1)
