"""Isolated contract tests for the E5 backup and restore entry points."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
REVISION = "a" * 40


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "media").mkdir()
        (self.media / "media" / "asset.txt").write_text("private asset\n")
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.db_marker = self.root / "restored-db"
        self._fake_tool("pg_dump", """#!/bin/sh
set -eu
for arg in "$@"; do case "$arg" in --file=*) printf PGDMP-synthetic > "${arg#--file=}";; esac; done
""")
        self._fake_tool("pg_restore", """#!/bin/sh
set -eu
if [ "$1" = --list ]; then
  test "$(head -c 5 "$2")" = PGDMP
else
  printf restored > "$E5_TEST_DB_MARKER"
fi
""")
        self._fake_tool("createdb", """#!/bin/sh
set -eu
test ! -e "$E5_TEST_DB_MARKER"
printf created > "$E5_TEST_DB_MARKER"
""")
        self._fake_tool("psql", "#!/bin/sh\nprintf '0\\n'\n")

    def _fake_tool(self, name: str, body: str) -> None:
        path = self.tools / name
        path.write_text(body)
        path.chmod(0o700)

    def _env(self) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{self.tools}:{os.environ['PATH']}",
            "PGHOST": "127.0.0.1",
            "PGUSER": "synthetic",
            "PGDATABASE": "synthetic_source",
            "PGPASSWORD": "synthetic_only",
            "MALL_MEDIA_ROOT": str(self.media),
            "E5_BACKUP_ROOT": str(self.backups),
            "MALL_RELEASE_REVISION": REVISION,
            "E5_WRITERS_PAUSED": "1",
            "E5_TEST_DB_MARKER": str(self.db_marker),
        }

    def _run(self, script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPTS / script), *args],
            env=env or self._env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def _backup(self) -> Path:
        result = self._run("e5-backup.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(result.stdout.strip())

    def test_backup_requires_writer_pause_and_uses_private_hashed_bundle(self) -> None:
        denied = self._run("e5-backup.sh", env={**self._env(), "E5_WRITERS_PAUSED": "0"})
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(list(self.backups.iterdir()), [])

        bundle = self._backup()
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)
        self.assertEqual((bundle / "database.dump").stat().st_mode & 0o777, 0o600)
        self.assertEqual((bundle / "media.tar").stat().st_mode & 0o777, 0o600)
        manifest = dict(line.split("=", 1) for line in (bundle / "manifest.txt").read_text().splitlines())
        self.assertEqual(manifest["format"], "e5-paired-v1")
        self.assertEqual(manifest["release_revision"], REVISION)
        self.assertTrue(manifest["consistent_at_utc"].endswith("Z"))
        self.assertEqual(manifest["database_client_sessions_before"], "0")
        self.assertEqual(manifest["database_client_sessions_after"], "0")
        for filename, field in (("database.dump", "database_sha256"), ("media.tar", "media_sha256")):
            self.assertEqual(manifest[field], hashlib.sha256((bundle / filename).read_bytes()).hexdigest())

    def test_restore_refuses_tamper_and_existing_targets(self) -> None:
        bundle = self._backup()
        target = self.root / "target-media"
        env = {**self._env(), "E5_RESTORE_DB": "isolated_restore", "E5_RESTORE_MEDIA_ROOT": str(target)}
        (bundle / "media.tar").write_bytes(b"tampered")
        rejected = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(self.db_marker.exists())
        self.assertFalse(target.exists())

        bundle = self._backup()
        target.mkdir()
        (target / "existing").write_text("keep")
        rejected = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual((target / "existing").read_text(), "keep")
        self.assertFalse(self.db_marker.exists())

    def test_restore_verified_bundle_into_new_isolated_targets(self) -> None:
        bundle = self._backup()
        target = self.root / "target-media"
        env = {**self._env(), "E5_RESTORE_DB": "isolated_restore", "E5_RESTORE_MEDIA_ROOT": str(target)}
        result = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / "media" / "asset.txt").read_text(), "private asset\n")
        self.assertEqual(self.db_marker.read_text(), "restored")
        again = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual((target / "media" / "asset.txt").read_text(), "private asset\n")

    def test_backup_refuses_symlink_in_media(self) -> None:
        (self.media / "media" / "link").symlink_to("/etc/passwd")
        result = self._run("e5-backup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_backup_failure_does_not_publish_partial_bundle(self) -> None:
        self._fake_tool("pg_dump", "#!/bin/sh\nexit 9\n")
        result = self._run("e5-backup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_backup_refuses_extra_database_client_before_or_after_capture(self) -> None:
        self._fake_tool("psql", "#!/bin/sh\nprintf '1\\n'\n")
        before = self._run("e5-backup.sh")
        self.assertNotEqual(before.returncode, 0)
        self.assertEqual(list(self.backups.iterdir()), [])

        marker = self.root / "client-check-count"
        self._fake_tool("psql", f"""#!/bin/sh
if [ -e '{marker}' ]; then printf '1\\n'; else : > '{marker}'; printf '0\\n'; fi
""")
        after = self._run("e5-backup.sh")
        self.assertNotEqual(after.returncode, 0)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_backup_accepts_secret_file_without_password_in_environment(self) -> None:
        secret = self.root / "postgres_password"
        secret.write_text("synthetic_only")
        env = self._env()
        env.pop("PGPASSWORD")
        env["PGPASSWORD_FILE"] = str(secret)
        result = self._run("e5-backup.sh", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restore_rejects_forged_archive_link_even_with_matching_hash(self) -> None:
        bundle = self._backup()
        archive = bundle / "media.tar"
        with tarfile.open(archive, "w") as target:
            entry = tarfile.TarInfo("./media/link")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            target.addfile(entry)
        manifest = bundle / "manifest.txt"
        data = manifest.read_text().splitlines()
        data = [
            f"media_sha256={hashlib.sha256(archive.read_bytes()).hexdigest()}" if line.startswith("media_sha256=")
            else f"media_bytes={archive.stat().st_size}" if line.startswith("media_bytes=")
            else line for line in data
        ]
        manifest.write_text("\n".join(data) + "\n")
        target = self.root / "target-media"
        env = {**self._env(), "E5_RESTORE_DB": "isolated_restore", "E5_RESTORE_MEDIA_ROOT": str(target)}
        result = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())
        self.assertFalse(self.db_marker.exists())

    def test_restore_requires_quiescent_database_evidence(self) -> None:
        bundle = self._backup()
        manifest = bundle / "manifest.txt"
        manifest.write_text(manifest.read_text().replace("database_client_sessions_after=0", "database_client_sessions_after=1"))
        target = self.root / "target-media"
        env = {**self._env(), "E5_RESTORE_DB": "isolated_restore", "E5_RESTORE_MEDIA_ROOT": str(target)}
        result = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.db_marker.exists())

    def test_restore_refuses_existing_database_without_touching_empty_media(self) -> None:
        bundle = self._backup()
        self.db_marker.write_text("existing")
        target = self.root / "target-media"
        target.mkdir()
        env = {**self._env(), "E5_RESTORE_DB": "isolated_restore", "E5_RESTORE_MEDIA_ROOT": str(target)}
        result = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.db_marker.read_text(), "existing")
        self.assertEqual(list(target.iterdir()), [])

    def test_restore_refuses_source_media_root_even_when_empty(self) -> None:
        bundle = self._backup()
        for file in self.media.rglob("*"):
            if file.is_file():
                file.unlink()
        (self.media / "media").rmdir()
        env = {**self._env(), "E5_RESTORE_DB": "isolated_restore", "E5_RESTORE_MEDIA_ROOT": str(self.media)}
        result = self._run("e5-restore.sh", str(bundle), env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.db_marker.exists())


if __name__ == "__main__":
    unittest.main()
