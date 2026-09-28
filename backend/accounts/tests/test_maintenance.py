"""E5 deployment maintenance bounds and no-provider default."""

from datetime import timedelta
from io import StringIO
import tempfile
from unittest.mock import patch

import psycopg

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount, AdminReadQuota
from checkout.models import CheckoutQuote


class QuotaCleanupTests(TestCase):
    def test_only_expired_buckets_are_removed_in_bounded_batches(self):
        actor = AdminAccount.objects.create_user("quota-cleaner", "Long test passphrase 2026!", kind="OWNER")
        old = timezone.now() - timedelta(days=2)
        recent = timezone.now().replace(second=0, microsecond=0)
        for index in range(3):
            AdminReadQuota.objects.create(actor=actor, scope="audit", window_start=old - timedelta(minutes=index))
        AdminReadQuota.objects.create(actor=actor, scope="audit", window_start=recent)

        call_command("purge_admin_read_quota", limit=2, stdout=StringIO())
        self.assertEqual(AdminReadQuota.objects.count(), 2)
        call_command("purge_admin_read_quota", limit=2, stdout=StringIO())
        self.assertEqual(AdminReadQuota.objects.count(), 1)
        self.assertTrue(AdminReadQuota.objects.filter(window_start=recent).exists())

    def test_rejects_unbounded_quota_cleanup(self):
        with self.assertRaises(CommandError):
            call_command("purge_admin_read_quota", limit=0, stdout=StringIO())


class LocalRecordCleanupTests(TestCase):
    def test_quote_cleanup_is_bounded_and_preserves_unexpired_quotes(self):
        from accounts.management.commands.run_maintenance_tick import purge_local_records

        for index in range(3):
            CheckoutQuote.objects.create(goods_total_fen=100, payable_fen=100,
                                         expires_at=timezone.now() - timedelta(minutes=index + 1))
        recent = CheckoutQuote.objects.create(goods_total_fen=100, payable_fen=100,
                                              expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(purge_local_records(2)["quotes"], 2)
        self.assertEqual(CheckoutQuote.objects.count(), 2)
        self.assertTrue(CheckoutQuote.objects.filter(pk=recent.pk).exists())


class MaintenanceTickTests(SimpleTestCase):
    @patch("accounts.management.commands.run_maintenance_tick.run_jobs")
    @patch("accounts.management.commands.run_maintenance_tick.maintenance_lock")
    def test_single_bounded_tick_never_dispatches_provider(self, lock, jobs):
        lock.return_value.__enter__.return_value = True
        jobs.return_value = {"closed": 1, "message_recovered": 0}
        output = StringIO()
        call_command("run_maintenance_tick", limit=7, stdout=output)
        jobs.assert_called_once_with(7)
        self.assertIn("closed=1", output.getvalue())

    @patch("accounts.management.commands.run_maintenance_tick.run_jobs")
    @patch("accounts.management.commands.run_maintenance_tick.maintenance_lock")
    def test_overlap_fails_without_starting_jobs(self, lock, jobs):
        lock.return_value.__enter__.return_value = False
        with self.assertRaises(CommandError):
            call_command("run_maintenance_tick", limit=7, stdout=StringIO())
        jobs.assert_not_called()

    def test_rejects_invalid_limit(self):
        with self.assertRaises(CommandError):
            call_command("run_maintenance_tick", limit=501, stdout=StringIO())

    @patch("accounts.management.commands.run_maintenance_tick.run_jobs")
    @patch("accounts.management.commands.run_maintenance_tick.maintenance_lock")
    def test_failed_job_makes_tick_exit_nonzero(self, lock, jobs):
        lock.return_value.__enter__.return_value = True
        jobs.return_value = {"closed": "FAILED:RuntimeError"}
        with self.assertRaisesRegex(CommandError, "maintenance jobs failed"):
            call_command("run_maintenance_tick", limit=2, stdout=StringIO())

    def test_lost_advisory_session_aborts_work(self):
        from accounts.management.commands.run_maintenance_tick import LOCK_CONNECTION, _ensure_lock_session

        token = LOCK_CONNECTION.set(object())
        try:
            with self.assertRaisesRegex(RuntimeError, "session was lost"):
                _ensure_lock_session()
        finally:
            LOCK_CONNECTION.reset(token)

    @patch("accounts.management.commands.run_maintenance_tick.purge_expired_orphans")
    @patch("accounts.management.commands.run_maintenance_tick.recover_expired_claims")
    @patch("accounts.management.commands.run_maintenance_tick.auto_confirm_receipts")
    @patch("accounts.management.commands.run_maintenance_tick.close_expired_orders")
    @patch("accounts.management.commands.run_maintenance_tick.call_command")
    @patch("accounts.management.commands.run_maintenance_tick.purge_local_records")
    def test_job_inputs_are_bounded_and_no_payment_reconcile_is_invoked(
            self, local, command, close, receipts, claims, media):
        from accounts.management.commands.run_maintenance_tick import run_jobs

        local.return_value = {"quotes": 0, "auth": 0}
        run_jobs(5)
        close.assert_called_once_with(5)
        receipts.assert_called_once_with(5)
        claims.assert_called_once_with(limit=5)
        media.assert_called_once_with(limit=5)
        self.assertEqual([item.args[0] for item in command.call_args_list],
                         ["expire_points", "purge_admin_read_quota"])
        self.assertTrue(all(item.kwargs["limit"] == 5 for item in command.call_args_list))

    @patch("accounts.management.commands.run_maintenance_tick.purge_expired_orphans")
    @patch("accounts.management.commands.run_maintenance_tick.recover_expired_claims")
    @patch("accounts.management.commands.run_maintenance_tick.auto_confirm_receipts")
    @patch("accounts.management.commands.run_maintenance_tick.close_expired_orders")
    @patch("accounts.management.commands.run_maintenance_tick.call_command")
    @patch("accounts.management.commands.run_maintenance_tick.purge_local_records")
    def test_failure_is_reported_but_other_safe_jobs_continue(
            self, local, command, close, receipts, claims, media):
        from accounts.management.commands.run_maintenance_tick import run_jobs

        close.side_effect = RuntimeError("local database failed")
        local.return_value = {"quotes": 0}
        with self.assertLogs("accounts.management.commands.run_maintenance_tick", level="ERROR"):
            outcomes = run_jobs(2)
        self.assertEqual(outcomes["closed"], "FAILED:RuntimeError")
        media.assert_called_once_with(limit=2)


class MaintenanceLockDatabaseTests(TransactionTestCase):
    def test_real_empty_tick_runs_without_provider_configuration(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            output = StringIO()
            call_command("run_maintenance_tick", limit=2, stdout=output)
        self.assertIn("closed=0", output.getvalue())
        self.assertIn("media_orphans=0", output.getvalue())
        self.assertNotIn("wechat", output.getvalue().lower())

    def test_second_database_session_cannot_run_same_tick(self):
        from accounts.management.commands.run_maintenance_tick import MAINTENANCE_LOCK

        with psycopg.connect(**connection.get_connection_params()) as contender:
            with contender.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", (MAINTENANCE_LOCK,))
                self.assertTrue(cursor.fetchone()[0])
            with self.assertRaisesRegex(CommandError, "already running"):
                call_command("run_maintenance_tick", limit=1, stdout=StringIO())
            with contender.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MAINTENANCE_LOCK,))
                self.assertTrue(cursor.fetchone()[0])
