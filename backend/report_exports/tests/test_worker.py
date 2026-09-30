import csv
import hashlib
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connections
from django.test import SimpleTestCase, TransactionTestCase
from django.core.management import call_command
from django.utils import timezone

from accounts.models import AdminAccount, AuditLog, GroupPermission, PermissionGroup
from report_exports.models import ExportTask
from report_exports.cleanup import purge_expired_exports
from report_exports.worker import claim_next, run_next
from report_exports import worker

from report_exports.worker import safe_csv_cell


class ExportCsvTests(SimpleTestCase):
    def test_formula_prefixes_and_leading_control_characters_are_escaped(self):
        for value in ("=SUM(1,1)", "+1", "-1", "@CMD", " \t=SUM(1,1)", "\ufeff =SUM(1,1)"):
            self.assertTrue(safe_csv_cell(value).startswith("'"), value)
        self.assertEqual(safe_csv_cell("普通商品"), "普通商品")

    def test_runner_releases_expired_capacity_before_claiming(self):
        events = []
        with patch("report_exports.management.commands.run_export_jobs.purge_expired_exports",
                   side_effect=lambda **kwargs: events.append("cleanup") or
                   {"expiredTasks": 0, "removedFiles": 0, "removedTemporaryFiles": 0}), \
             patch("report_exports.management.commands.run_export_jobs.run_next",
                   side_effect=lambda: events.append("claim") or None):
            call_command("run_export_jobs", limit=1)
        self.assertEqual(events, ["cleanup", "claim"])


class ExportWorkerTests(TransactionTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        settings = self.settings(MEDIA_ROOT=self.folder.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.actor = AdminAccount.objects.create_user("export-worker", "Synthetic password 2026!",
                                                      display_name="合成操作员", kind="OWNER")
        today = timezone.localdate().isoformat()
        self.filters = {"from": today, "to": today}

    def task(self, kind, filters=None):
        return ExportTask.objects.create(kind=kind, created_by=self.actor, request_key=uuid.uuid4(),
                                         filters=filters or self.filters, filters_digest="a" * 64)

    def test_business_file_is_durable_and_matches_summary_day(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        self.assertEqual(run_next(), task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.READY)
        self.assertEqual(task.row_count, 1)
        path = Path(self.folder.name) / task.object_key
        content = path.read_bytes()
        self.assertEqual(task.file_bytes, len(content))
        self.assertEqual(task.file_sha256, hashlib.sha256(content).hexdigest())
        rows = list(csv.reader(content.decode("utf-8-sig").splitlines()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][:3], [self.filters["from"], "0", "0"])
        self.assertGreater(task.expires_at, task.completed_at + timedelta(hours=23))

    def test_audit_export_redacts_private_values_and_escapes_formula(self):
        AuditLog.objects.create(actor=self.actor, action_code="product.update", object_type="product",
                                object_id="=FORMULA()", before={"password": "private-secret"},
                                after={"status": "ON_SALE", "unknown": "private-detail"},
                                result="SUCCESS", request_id=uuid.uuid4())
        task = self.task(ExportTask.Kind.AUDIT)
        self.assertEqual(run_next(), task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.READY)
        self.assertEqual(task.row_count, 1)
        content = (Path(self.folder.name) / task.object_key).read_bytes().decode("utf-8-sig")
        self.assertNotIn("private-secret", content)
        self.assertNotIn("private-detail", content)
        rows = list(csv.reader(content.splitlines()))
        self.assertEqual(rows[1][4], "'=FORMULA()")
        self.assertIn("已脱敏", rows[1][7])

    def test_stale_lease_recovers_without_a_second_live_claim(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        claimed = claim_next()
        self.assertEqual(claimed.id, task.id)
        self.assertIsNone(claim_next())
        ExportTask.objects.filter(pk=task.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(run_next(), task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.READY)
        self.assertEqual(task.attempt_count, 2)

    def test_file_cap_fails_without_downloadable_metadata(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        with self.settings(STORAGE_EXPORT_MAX_BYTES=10):
            self.assertEqual(run_next(), task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.FAILED)
        self.assertEqual(task.failure_code, "FILE_TOO_LARGE")
        self.assertEqual(task.object_key, "")
        self.assertFalse((Path(self.folder.name) / f"export/{task.id}.csv").exists())

    def test_expired_file_and_failed_orphan_are_cleaned_idempotently(self):
        ready = self.task(ExportTask.Kind.BUSINESS)
        self.assertEqual(run_next(), ready.id)
        ready.refresh_from_db()
        self.assertEqual(purge_expired_exports()["removedFiles"], 0)
        self.assertTrue((Path(self.folder.name) / ready.object_key).exists())
        ExportTask.objects.filter(pk=ready.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        failed = self.task(ExportTask.Kind.BUSINESS)
        ExportTask.objects.filter(pk=failed.pk).update(status=ExportTask.Status.FAILED,
                                                       completed_at=timezone.now() - timedelta(days=2))
        orphan = Path(self.folder.name) / f"export/{failed.id}.csv"
        orphan.write_bytes(b"orphan")
        result = purge_expired_exports()
        self.assertEqual(result["expiredTasks"], 1)
        self.assertGreaterEqual(result["removedFiles"], 2)
        ready.refresh_from_db()
        self.assertEqual(ready.status, ExportTask.Status.EXPIRED)
        self.assertFalse((Path(self.folder.name) / ready.object_key).exists())
        self.assertFalse(orphan.exists())
        self.assertEqual(purge_expired_exports()["removedFiles"], 0)

    def test_cleanup_failure_keeps_expired_task_for_retry(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        self.assertEqual(run_next(), task.id)
        ExportTask.objects.filter(pk=task.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with patch("report_exports.cleanup._delete", side_effect=OSError("synthetic disk error")):
            self.assertEqual(purge_expired_exports()["expiredTasks"], 0)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.READY)
        self.assertEqual(purge_expired_exports()["expiredTasks"], 1)
        self.assertFalse((Path(self.folder.name) / task.object_key).exists())

    def test_cleanup_rejects_invalid_limit_and_preserves_unexpected_filename(self):
        with self.assertRaises(ValueError):
            purge_expired_exports(limit=0)
        root = Path(self.folder.name) / "export"
        root.mkdir(mode=0o700)
        unexpected = root / "not-a-task.csv"
        unexpected.write_bytes(b"private")
        self.assertEqual(purge_expired_exports()["removedFiles"], 0)
        self.assertTrue(unexpected.exists())

    def test_interrupted_temporary_is_cleaned_but_active_lease_is_preserved(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        claimed = claim_next()
        temporary = Path(self.folder.name) / f"tmp/export-{task.id.hex}-{claimed.lease_token.hex}.upload"
        temporary.parent.mkdir(mode=0o700)
        temporary.write_bytes(b"partial private report")
        self.assertEqual(purge_expired_exports()["removedTemporaryFiles"], 0)
        self.assertTrue(temporary.exists())
        ExportTask.objects.filter(pk=task.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(purge_expired_exports()["removedTemporaryFiles"], 1)
        self.assertFalse(temporary.exists())

    def test_global_capacity_and_free_space_stop_generation_before_file_write(self):
        limited = self.task(ExportTask.Kind.BUSINESS)
        with self.settings(STORAGE_EXPORT_GLOBAL_MAX_BYTES=1):
            self.assertEqual(run_next(), limited.pk)
        limited.refresh_from_db()
        self.assertEqual((limited.status, limited.failure_code), (ExportTask.Status.FAILED, "CAPACITY_LIMIT"))
        low_disk = self.task(ExportTask.Kind.BUSINESS)
        with patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            self.assertEqual(run_next(), low_disk.pk)
        low_disk.refresh_from_db()
        self.assertEqual((low_disk.status, low_disk.failure_code), (ExportTask.Status.FAILED, "DISK_LOW"))
        self.assertFalse((Path(self.folder.name) / f"export/{low_disk.id}.csv").exists())

    def test_expired_lease_can_fail_capacity_check_without_staying_claimable(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        self.assertEqual(claim_next().pk, task.pk)
        ExportTask.objects.filter(pk=task.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        with self.settings(STORAGE_EXPORT_GLOBAL_MAX_BYTES=1), patch.object(worker, "_render") as render:
            self.assertEqual(run_next(), task.pk)
            render.assert_not_called()
        task.refresh_from_db()
        self.assertEqual((task.status, task.failure_code), (ExportTask.Status.FAILED, "CAPACITY_LIMIT"))
        self.assertIsNone(task.lease_token)
        self.assertIsNone(task.lease_until)
        self.assertIsNone(claim_next())

    def test_disk_reservation_includes_other_active_workers(self):
        active = self.task(ExportTask.Kind.BUSINESS)
        self.assertEqual(claim_next().pk, active.pk)
        waiting = self.task(ExportTask.Kind.BUSINESS)
        cap = 100 * 1024 * 1024
        free = 2 * cap + 256 * 1024 * 1024 - 1
        with patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=free)):
            self.assertEqual(run_next(), waiting.pk)
        waiting.refresh_from_db()
        self.assertEqual((waiting.status, waiting.failure_code), (ExportTask.Status.FAILED, "DISK_LOW"))

    def test_revoked_creator_cannot_consume_capacity_or_generate_file(self):
        staff = AdminAccount.objects.create_user("export-revoked", "Synthetic password 2026!",
                                                 display_name="员工", kind="STAFF")
        group = PermissionGroup.objects.create(code="export-worker-group", name="导出")
        GroupPermission.objects.create(group=group, code="business.report.read")
        grant = GroupPermission.objects.create(group=group, code="business.report.export")
        staff.permission_groups.add(group)
        task = ExportTask.objects.create(kind=ExportTask.Kind.BUSINESS, created_by=staff,
                                         request_key=uuid.uuid4(), filters=self.filters, filters_digest="b" * 64)
        grant.delete()
        self.assertEqual(run_next(), task.pk)
        task.refresh_from_db()
        self.assertEqual((task.status, task.failure_code), (ExportTask.Status.FAILED, "PERMISSION_REVOKED"))

    def test_row_cap_fails_without_installing_file(self):
        for item in range(2):
            AuditLog.objects.create(actor=self.actor, action_code="product.update", object_type="product",
                                    object_id=str(item), result="SUCCESS", request_id=uuid.uuid4())
        task = self.task(ExportTask.Kind.AUDIT)
        with patch.object(worker, "MAX_AUDIT_ROWS", 1):
            self.assertEqual(run_next(), task.pk)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.FAILED)
        self.assertEqual(task.failure_code, "ROW_LIMIT")
        self.assertFalse((Path(self.folder.name) / f"export/{task.id}.csv").exists())

    def test_install_then_database_failure_leaves_only_unreachable_orphan(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        original_save = ExportTask.save

        def fail_ready(instance, *args, **kwargs):
            if instance.status == ExportTask.Status.READY:
                raise RuntimeError("synthetic database write failure")
            return original_save(instance, *args, **kwargs)

        with patch.object(ExportTask, "save", fail_ready):
            self.assertEqual(run_next(), task.pk)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.FAILED)
        self.assertEqual(task.object_key, "")
        orphan = Path(self.folder.name) / f"export/{task.id}.csv"
        self.assertTrue(orphan.exists())
        self.assertEqual(purge_expired_exports()["removedFiles"], 1)
        self.assertFalse(orphan.exists())

    def test_two_workers_do_not_claim_one_active_task(self):
        task = self.task(ExportTask.Kind.BUSINESS)
        entered, release = Event(), Event()
        real_render = worker._render

        def hold_render(*args):
            entered.set()
            self.assertTrue(release.wait(10))
            return real_render(*args)

        def run_with_closed_connection():
            try:
                return run_next()
            finally:
                connections.close_all()

        with patch.object(worker, "_render", hold_render), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run_with_closed_connection)
            self.assertTrue(entered.wait(10))
            self.assertIsNone(pool.submit(run_with_closed_connection).result(timeout=10))
            release.set()
            self.assertEqual(first.result(timeout=10), task.pk)
        task.refresh_from_db()
        self.assertEqual(task.status, ExportTask.Status.READY)
        self.assertEqual(task.attempt_count, 1)
