"""E3.1 committed-source proof and fail-closed deployment sync."""

import io
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from code_versions.models import CodeBuildJob, CodeSourceProvenance, CodeVersion
from code_versions.package import PackageError
from code_versions.service import build_local_version, build_trusted_version
from code_versions.trusted_source import build_git_package
from code_versions.views import _version, _versions


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout.decode().strip()


class GitSourceMixin:
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = Path(folder.name).resolve()
        self.source = self.repo / "mini-program"
        self.source.mkdir()
        (self.source / "app.js").write_text("App({})", encoding="utf-8")
        (self.source / "app.json").write_text("{}", encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "add", "mini-program")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "source")
        self.revision = git(self.repo, "rev-parse", "HEAD")


class TrustedSourceTests(GitSourceMixin, SimpleTestCase):
    def test_package_comes_from_exact_commit_and_is_repeatable(self):
        first = build_git_package(self.repo, self.revision, 100_000)
        second = build_git_package(self.repo, self.revision, 100_000)
        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first.data)) as archive:
            self.assertEqual(archive.read("app.js"), b"App({})")

    def test_ignored_tracked_files_do_not_consume_package_limit(self):
        (self.source / "tests").mkdir()
        (self.source / "tests" / "large.js").write_bytes(b"x" * 1000)
        git(self.repo, "add", "mini-program/tests/large.js")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "ignored test")
        package = build_git_package(self.repo, git(self.repo, "rev-parse", "HEAD"), 500)
        with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
            self.assertEqual(archive.namelist(), ["app.js", "app.json"])

    def test_dirty_checkout_and_wrong_revision_fail_closed(self):
        (self.source / "app.js").write_text("App({dirty:true})", encoding="utf-8")
        with self.assertRaises(PackageError) as raised:
            build_git_package(self.repo, self.revision, 100_000)
        self.assertEqual(raised.exception.code, "SOURCE_DIRTY")
        (self.source / "app.js").write_text("App({})", encoding="utf-8")
        with self.assertRaises(PackageError) as raised:
            build_git_package(self.repo, "a" * 40, 100_000)
        self.assertEqual(raised.exception.code, "SOURCE_REVISION_INVALID")

    def test_untracked_source_and_symlink_are_rejected(self):
        (self.source / "extra.js").write_text("x", encoding="utf-8")
        with self.assertRaises(PackageError) as raised:
            build_git_package(self.repo, self.revision, 100_000)
        self.assertEqual(raised.exception.code, "SOURCE_DIRTY")
        (self.source / "extra.js").unlink()
        (self.source / "pages" / "home").mkdir(parents=True)
        (self.source / "pages" / "home" / "link.js").symlink_to(self.source / "app.js")
        git(self.repo, "add", "mini-program/pages/home/link.js")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "symlink")
        with self.assertRaises(PackageError) as raised:
            build_git_package(self.repo, git(self.repo, "rev-parse", "HEAD"), 100_000)
        self.assertEqual(raised.exception.code, "SOURCE_INVALID")

    def test_git_replace_ref_cannot_substitute_committed_bytes(self):
        (self.source / "app.js").write_text("App({substituted:true})", encoding="utf-8")
        git(self.repo, "add", "mini-program/app.js")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "replacement")
        replacement = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "reset", "--hard", self.revision)
        git(self.repo, "replace", self.revision, replacement)
        package = build_git_package(self.repo, self.revision, 100_000)
        with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
            self.assertEqual(archive.read("app.js"), b"App({})")
        git(self.repo, "reset", "--hard", "HEAD")
        with self.assertRaises(PackageError) as raised:
            build_git_package(self.repo, self.revision, 100_000)
        self.assertEqual(raised.exception.code, "SOURCE_DIRTY")

    def test_external_git_environment_cannot_redirect_repository(self):
        with patch.dict(os.environ, {"GIT_DIR": "/missing/git-dir",
                                  "GIT_WORK_TREE": "/missing/work-tree"}):
            package = build_git_package(self.repo, self.revision, 100_000)
        with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
            self.assertEqual(archive.read("app.js"), b"App({})")


class TrustedVersionTests(GitSourceMixin, TestCase):
    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        setting = override_settings(MINIPROGRAM_SOURCE_ROOT=self.source, MEDIA_ROOT=self.media)
        setting.enable()
        self.addCleanup(setting.disable)

    def test_trusted_build_attests_existing_local_version_without_mutating_it(self):
        local = build_local_version()
        self.assertEqual(local.source_revision, "")
        trusted = build_trusted_version(self.repo, self.revision)
        self.assertEqual(trusted.pk, local.pk)
        local.refresh_from_db()
        self.assertEqual(local.source_revision, "")
        proof = CodeSourceProvenance.objects.get(version=local)
        self.assertEqual(proof.source_revision, self.revision)
        self.assertEqual(CodeVersion.objects.count(), 1)
        self.assertEqual(CodeBuildJob.objects.filter(status="SUCCEEDED").count(), 2)
        self.assertEqual(CodeBuildJob.objects.latest("created_at").source_revision, self.revision)
        repeated = build_trusted_version(self.repo, self.revision)
        self.assertEqual(repeated.pk, local.pk)
        self.assertEqual(CodeSourceProvenance.objects.count(), 1)
        (self.repo / "README.md").write_text("new release", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "unrelated")
        later_revision = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(build_trusted_version(self.repo, later_revision).pk, local.pk)
        self.assertEqual(CodeSourceProvenance.objects.count(), 2)
        self.assertEqual(_version(_versions().get())["sourceRevision"], later_revision)
        with self.assertRaises(DatabaseError), transaction.atomic():
            CodeSourceProvenance.objects.filter(source_revision=self.revision).update(
                source_revision="a" * 40)

    def test_deployment_command_syncs_pinned_revision_and_rejects_dirty_source(self):
        with override_settings(BASE_DIR=self.repo / "backend"):
            call_command("sync_deployed_miniprogram", expected_revision=self.revision)
            self.assertEqual(CodeSourceProvenance.objects.get().source_revision, self.revision)
            (self.source / "app.js").write_text("dirty", encoding="utf-8")
            with self.assertRaises(CommandError) as raised:
                call_command("sync_deployed_miniprogram", expected_revision=self.revision)
        self.assertIn("SOURCE_DIRTY", str(raised.exception))
        self.assertEqual(CodeBuildJob.objects.filter(status="SUCCEEDED").count(), 1)
        self.assertEqual(CodeBuildJob.objects.filter(status="FAILED").count(), 1)

    def test_failed_provenance_has_no_success_job_or_proof(self):
        (self.source / "app.js").write_text("dirty", encoding="utf-8")
        with self.assertRaises(PackageError):
            build_trusted_version(self.repo, self.revision)
        self.assertEqual(CodeVersion.objects.count(), 0)
        self.assertEqual(CodeSourceProvenance.objects.count(), 0)
        self.assertEqual(CodeBuildJob.objects.get().failure_code, "SOURCE_DIRTY")
        self.assertEqual(CodeBuildJob.objects.get().source_revision, self.revision)

    def test_storage_failure_keeps_provenance_absent(self):
        with patch("code_versions.service.LocalStorage.promote", side_effect=OSError("full")):
            with self.assertRaises(Exception):
                build_trusted_version(self.repo, self.revision)
        self.assertEqual(CodeVersion.objects.count(), 0)
        self.assertEqual(CodeSourceProvenance.objects.count(), 0)
        self.assertEqual(CodeBuildJob.objects.get().failure_code, "STORAGE_UNAVAILABLE")
        self.assertEqual(CodeBuildJob.objects.get().source_revision, self.revision)

    def test_staging_failure_is_not_reported_as_persistent_storage_failure(self):
        with patch("code_versions.trusted_source.tempfile.TemporaryDirectory",
                   side_effect=OSError("temporary volume unavailable")):
            with self.assertRaises(PackageError) as raised:
                build_trusted_version(self.repo, self.revision)
        self.assertEqual(raised.exception.code, "SOURCE_UNAVAILABLE")
        self.assertEqual(CodeBuildJob.objects.get().failure_code, "SOURCE_UNAVAILABLE")
        self.assertEqual(CodeSourceProvenance.objects.count(), 0)

    def test_staging_location_cannot_be_inside_release_checkout(self):
        with patch("code_versions.trusted_source.tempfile.gettempdir", return_value=str(self.repo)):
            with self.assertRaises(PackageError) as raised:
                build_trusted_version(self.repo, self.revision)
        self.assertEqual(raised.exception.code, "SOURCE_UNAVAILABLE")
        self.assertEqual(CodeSourceProvenance.objects.count(), 0)


class DeployHookTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.log = self.folder / "calls"
        self.launched = self.folder / "launched"
        self.fake_python = self.folder / "python"
        self.fake_python.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$2" >> "$MALL_TEST_LOG"\n'
            'if [ "$2" = "$MALL_TEST_FAIL_STEP" ]; then exit 41; fi\n', encoding="utf-8")
        self.fake_python.chmod(0o700)
        self.release = self.folder / "release"
        (self.release / "scripts").mkdir(parents=True)
        (self.release / "backend").mkdir()
        self.hook = self.release / "scripts" / "deploy-with-code-sync.sh"
        self.hook.write_bytes((settings.BASE_DIR.parent / "scripts" /
                               "deploy-with-code-sync.sh").read_bytes())
        self.hook.chmod(0o700)
        (self.release / "backend" / "manage.py").write_text("# managed by fake python\n")
        git(self.release, "init", "-q")
        git(self.release, "add", "scripts", "backend")
        git(self.release, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "release")
        self.revision = git(self.release, "rev-parse", "HEAD")

    def run_hook(self, failure=""):
        env = {**os.environ, "MALL_RELEASE_REVISION": self.revision,
               "MALL_PYTHON": str(self.fake_python), "MALL_TEST_LOG": str(self.log),
               "MALL_TEST_FAIL_STEP": failure, "MALL_TEST_LAUNCHED": str(self.launched)}
        return subprocess.run([str(self.hook), "sh", "-c", 'touch "$MALL_TEST_LAUNCHED"'],
                              env=env, capture_output=True, timeout=10)

    def test_migration_or_sync_failure_prevents_launch(self):
        for failure, expected in [("migrate", ["migrate"]),
                                  ("sync_deployed_miniprogram", ["migrate", "sync_deployed_miniprogram"])]:
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                result = self.run_hook(failure)
                self.assertEqual(result.returncode, 41)
                self.assertEqual(self.log.read_text().splitlines(), expected)
                self.assertFalse(self.launched.exists())

    def test_success_runs_migrate_then_sync_then_launch(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(),
                         ["migrate", "sync_deployed_miniprogram"])
        self.assertTrue(self.launched.exists())

    def test_dirty_release_is_rejected_before_migration(self):
        (self.release / "backend" / "manage.py").write_text("dirty\n")
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.launched.exists())
