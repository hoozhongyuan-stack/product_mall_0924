"""E3.0 private, reproducible source snapshots and local persistence."""

import io
import hashlib
import json
import secrets
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from code_versions.models import CodeBuildJob, CodeVersion
from code_versions.package import PackageError, build_package
from code_versions import package as package_module
from code_versions.service import build_local_version


class PackageTests(SimpleTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "pages" / "home").mkdir(parents=True)
        (self.root / "pages" / "home" / "home.js").write_text("Page({})", encoding="utf-8")
        (self.root / "app.js").write_text("App({})", encoding="utf-8")
        (self.root / "app.json").write_text("{}", encoding="utf-8")
        (self.root / "project.config.json").write_text('{"appid":"touristappid"}', encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "ignored.js").write_text("secret", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "ignored.js").write_text("secret", encoding="utf-8")
        (self.root / "pages" / "home" / "node_modules").mkdir()
        (self.root / "pages" / "home" / "node_modules" / "ignored.js").write_text("secret2", encoding="utf-8")
        (self.root / "pages" / "home" / ".private.js").write_text("secret3", encoding="utf-8")
        (self.root / "project.private.config.json").write_text("private-key", encoding="utf-8")

    def test_repeatable_package_and_private_files_excluded(self):
        first = build_package(self.root, 100_000)
        second = build_package(self.root, 100_000)
        self.assertEqual((first.source_digest, first.package_sha256, first.data),
                         (second.source_digest, second.package_sha256, second.data))
        with zipfile.ZipFile(io.BytesIO(first.data)) as archive:
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
            self.assertIn("pages/home/home.js", archive.namelist())
            self.assertNotIn("project.private.config.json", archive.namelist())
            self.assertNotIn("tests/ignored.js", archive.namelist())
            self.assertNotIn("node_modules/ignored.js", archive.namelist())
            self.assertNotIn("pages/home/node_modules/ignored.js", archive.namelist())
            self.assertNotIn("pages/home/.private.js", archive.namelist())
            self.assertNotIn(b"private-key", first.data)

    def test_source_change_changes_digest_and_oversize_rejected(self):
        before = build_package(self.root, 100_000)
        (self.root / "app.js").write_text("App({changed:true})", encoding="utf-8")
        self.assertNotEqual(before.source_digest, build_package(self.root, 100_000).source_digest)
        with self.assertRaises(PackageError) as raised:
            build_package(self.root, 10)
        self.assertEqual(raised.exception.code, "PACKAGE_TOO_LARGE")

    def test_symlink_rejected_and_missing_source_fails_closed(self):
        (self.root / "lib").mkdir()
        (self.root / "lib" / "link.js").symlink_to(self.root / "app.js")
        with self.assertRaises(PackageError) as raised:
            build_package(self.root, 100_000)
        self.assertEqual(raised.exception.code, "SOURCE_INVALID")
        with self.assertRaises(PackageError):
            build_package(self.root / "missing", 100_000)

    def test_change_during_first_pass_rejects_mixed_tree(self):
        original = package_module._safe_read
        changed = False

        def mutate(path, root, maximum_bytes):
            nonlocal changed
            content = original(path, root, maximum_bytes)
            if path.name == "app.json" and not changed:
                (self.root / "app.js").write_text("App({changed:true})", encoding="utf-8")
                changed = True
            return content

        with patch("code_versions.package._safe_read", side_effect=mutate):
            with self.assertRaises(PackageError) as raised:
                build_package(self.root, 100_000)
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED")

    def test_parent_symlink_is_not_traversed(self):
        link = self.root.parent / f"mini-source-link-{secrets.token_hex(8)}"
        link.symlink_to(self.root.parent, target_is_directory=True)
        self.addCleanup(link.unlink)
        with self.assertRaises(PackageError):
            build_package(link / self.root.name, 100_000)


class VersionPersistenceTests(TestCase):
    def setUp(self):
        source = tempfile.TemporaryDirectory()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(source.cleanup)
        self.addCleanup(media.cleanup)
        self.source = Path(source.name).resolve()
        self.media = Path(media.name)
        (self.source / "app.js").write_text("App({})", encoding="utf-8")
        (self.source / "app.json").write_text("{}", encoding="utf-8")
        setting = override_settings(MINIPROGRAM_SOURCE_ROOT=self.source, MEDIA_ROOT=self.media)
        setting.enable()
        self.addCleanup(setting.disable)

    def test_command_builds_once_and_records_local_fact_only(self):
        call_command("build_miniprogram_source")
        first = CodeVersion.objects.get()
        self.assertEqual(first.storage_status, "READY")
        self.assertEqual(first.platform_status, "NOT_CONFIGURED")
        self.assertEqual(first.source_revision, "")
        self.assertTrue((self.media / first.object_key).is_file())
        self.assertEqual((self.media / first.object_key).stat().st_mode & 0o777, 0o600)
        with zipfile.ZipFile(self.media / first.object_key) as archive:
            self.assertEqual(archive.namelist(), ["app.js", "app.json"])
        call_command("build_miniprogram_source")
        self.assertEqual(CodeVersion.objects.count(), 1)
        self.assertEqual(CodeBuildJob.objects.filter(status="SUCCEEDED").count(), 2)

    def test_storage_failure_recorded_without_ready_version_and_retry_works(self):
        with patch("code_versions.service.LocalStorage.promote", side_effect=OSError("disk full")):
            with self.assertRaises(Exception):
                build_local_version()
        self.assertEqual(CodeVersion.objects.count(), 0)
        failed = CodeBuildJob.objects.get()
        self.assertEqual((failed.status, failed.failure_code), ("FAILED", "STORAGE_UNAVAILABLE"))
        self.assertNotIn("disk full", str(failed.__dict__))
        version = build_local_version()
        self.assertEqual(version.storage_status, "READY")
        self.assertEqual(CodeBuildJob.objects.filter(status="SUCCEEDED").count(), 1)

    def test_missing_source_retains_failure_job(self):
        with override_settings(MINIPROGRAM_SOURCE_ROOT=self.source / "missing"):
            with self.assertRaises(PackageError):
                build_local_version()
        self.assertEqual(CodeVersion.objects.count(), 0)
        self.assertEqual(CodeBuildJob.objects.get().failure_code, "SOURCE_UNAVAILABLE")

    def test_stale_started_job_becomes_interrupted_on_next_command(self):
        stale = CodeBuildJob.objects.create()
        build_local_version()
        stale.refresh_from_db()
        self.assertEqual((stale.status, stale.failure_code), ("FAILED", "INTERRUPTED"))
        self.assertIsNotNone(stale.completed_at)

    def test_database_failure_after_install_reuses_verified_orphan(self):
        with patch("code_versions.service.CodeVersion.objects.create", side_effect=DatabaseError("transient")):
            with self.assertRaises(Exception):
                build_local_version()
        self.assertEqual(CodeVersion.objects.count(), 0)
        self.assertEqual(CodeBuildJob.objects.get().failure_code, "DATABASE_UNAVAILABLE")
        objects = list((self.media / "code").glob("*.zip"))
        self.assertEqual(len(objects), 1)
        before = objects[0].stat().st_ino
        version = build_local_version()
        self.assertEqual(version.object_key, f"code/{objects[0].name}")
        self.assertEqual(objects[0].stat().st_ino, before)

    def test_version_row_cannot_be_mutated_or_deleted(self):
        version = build_local_version()
        with self.assertRaises(DatabaseError), transaction.atomic():
            CodeVersion.objects.filter(pk=version.pk).update(version_label="changed")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM mini_code_version WHERE id = %s", [version.pk])


class VersionApiTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        setting = override_settings(MEDIA_ROOT=self.media)
        setting.enable()
        self.addCleanup(setting.disable)
        self.password = secrets.token_urlsafe(24)
        self.owner = AdminAccount.objects.create_user(
            "code-version-owner", self.password, display_name="主账号", kind="OWNER")
        self.client = self._login(self.owner)

    def _login(self, account):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        response = client.post("/api/v1/admin/auth/login",
                               data=json.dumps({"loginName": account.login_name, "password": self.password}),
                               content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200, response.content)
        return client

    def test_list_detail_job_permissions_and_no_object_key(self):
        version = CodeVersion.objects.create(
            version_label="local-abc", source_digest="a" * 64, package_sha256="b" * 64,
            package_bytes=100, file_count=2, object_key="code/private.zip",
            storage_status="READY", platform_status="NOT_CONFIGURED")
        CodeBuildJob.objects.create(status="FAILED", failure_code="SOURCE_INVALID",
                                    completed_at=timezone.now())
        list_response = self.client.get("/api/v1/admin/code-versions")
        self.assertEqual(list_response.status_code, 200, list_response.content)
        self.assertIn("no-store", list_response["Cache-Control"])
        self.assertEqual(list_response.json()["data"]["items"][0]["versionId"], str(version.pk))
        detail = self.client.get(f"/api/v1/admin/code-versions/{version.pk}")
        jobs = self.client.get("/api/v1/admin/code-sync-jobs")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(jobs.status_code, 200, jobs.content)
        self.assertEqual(jobs.json()["data"]["items"][0]["failureCode"], "SOURCE_INVALID")
        self.assertNotIn("code/private.zip", list_response.content.decode() + detail.content.decode())
        self.assertEqual(Client().get("/api/v1/admin/code-versions").status_code, 401)
        group = PermissionGroup.objects.create(code="code-reader", name="代码读者")
        account = AdminAccount.objects.create_user("code-staff", self.password, display_name="员工")
        AccountGroup.objects.create(account=account, group=group)
        staff = self._login(account)
        self.assertEqual(staff.get("/api/v1/admin/code-versions").status_code, 403)
        GroupPermission.objects.create(group=group, code="code.version.read")
        self.assertEqual(staff.get("/api/v1/admin/code-versions").status_code, 200)
        self.assertEqual(staff.get("/api/v1/admin/code-sync-jobs").status_code, 200)

    def test_jobs_cursor_is_stable_and_not_usable_for_versions(self):
        for _ in range(3):
            CodeBuildJob.objects.create(status="FAILED", failure_code="SOURCE_INVALID",
                                        completed_at=timezone.now())
        first = self.client.get("/api/v1/admin/code-sync-jobs?limit=2")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(len(first.json()["data"]["items"]), 2)
        cursor = first.json()["data"]["nextCursor"]
        self.assertTrue(cursor)
        second = self.client.get("/api/v1/admin/code-sync-jobs?limit=2&cursor=" + cursor)
        self.assertEqual(len(second.json()["data"]["items"]), 1)
        self.assertIsNone(second.json()["data"]["nextCursor"])
        self.assertEqual(self.client.get("/api/v1/admin/code-versions?cursor=" + cursor).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/admin/code-versions?limit=101").status_code, 400)

    def test_read_status_checks_private_object_without_exposing_key(self):
        folder = self.media / "code"
        folder.mkdir(mode=0o700)
        path = folder / "object.zip"
        path.write_bytes(b"abc")
        version = CodeVersion.objects.create(
            version_label="source-abc", source_digest="c" * 64,
            package_sha256=hashlib.sha256(b"abc").hexdigest(), package_bytes=3,
            file_count=1, object_key="code/object.zip",
            storage_status="READY", platform_status="NOT_CONFIGURED")
        list_path = "/api/v1/admin/code-versions"
        detail_path = f"{list_path}/{version.pk}"
        self.assertEqual(self.client.get(list_path).json()["data"]["items"][0]["storageStatus"],
                         "STORED_UNVERIFIED")
        self.assertEqual(self.client.get(detail_path).json()["data"]["storageStatus"], "READY")
        path.write_bytes(b"xyz")
        self.assertEqual(self.client.get(detail_path).json()["data"]["storageStatus"], "UNAVAILABLE")
        path.unlink()
        self.assertEqual(self.client.get(list_path).json()["data"]["items"][0]["storageStatus"],
                         "UNAVAILABLE")
