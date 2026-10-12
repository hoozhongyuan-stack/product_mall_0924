"""Derived cache paths preserve SHA-256 entropy and pass E5 media preflight."""
import base64
import re
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from catalog import thumbnails
from catalog.media import asset_path
from catalog.tests import test_media_thumbnails as fixtures


def e5_media_preflight(root):
    script = (Path(__file__).resolve().parents[3] / "scripts/e5-backup.sh").read_text()
    expression = re.search(r"perl -MFile::Find -e '([\s\S]+?)' \"\$MALL_MEDIA_ROOT\"", script).group(1)
    return subprocess.run(["perl", "-MFile::Find", "-e", expression, str(root)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


class ThumbnailBackupPathTests(SimpleTestCase):
    setUp = fixtures.ThumbnailStorageTests.setUp
    thumbnail = fixtures.ThumbnailStorageTests.thumbnail

    def assert_backup_safe(self):
        root = Path(self.directory.name)
        checked = e5_media_preflight(root)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode())
        paths = [str(path.relative_to(root)) for path in root.rglob("*")]
        self.assertTrue(paths)
        for path in paths:
            self.assertLessEqual(len(path.encode("ascii")), 98)
            self.assertRegex(path, r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")

    def test_complete_cache_and_inflight_temporary_paths_pass_real_e5_preflight(self):
        generate = thumbnails.generate_thumbnail
        def checked(*args):
            self.assertTrue(list(Path(self.directory.name).rglob("*.part")))
            self.assert_backup_safe()
            return generate(*args)
        with patch("catalog.thumbnails.generate_thumbnail", side_effect=checked):
            result = self.thumbnail()
        self.assert_backup_safe()
        self.assertEqual(result.parent.name, self.asset.id.hex)
        fingerprint = thumbnails.source_state(self.asset)[1]
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(len(result.stem), 43)
        self.assertEqual(base64.urlsafe_b64decode(result.stem + "="), bytes.fromhex(fingerprint))

    def test_delete_cleanup_handles_only_this_assets_compact_and_legacy_derivatives(self):
        result = self.thumbnail()
        root = Path(self.directory.name) / ".thumbnails/v1"
        legacy = root / str(self.asset.id)
        legacy.mkdir(parents=True, exist_ok=True)
        old = legacy / ("a" * 64 + ".jpg")
        old.write_bytes(b"old derivative")
        other = root / ("b" * 32)
        other.mkdir()
        retained = other / "retain.jpg"
        retained.write_bytes(b"other asset derivative")
        original = asset_path(self.asset).read_bytes()
        thumbnails.discard_asset_thumbnails(self.asset.id)
        self.assertFalse(result.exists())
        self.assertFalse(old.exists())
        self.assertEqual(retained.read_bytes(), b"other asset derivative")
        self.assertEqual(asset_path(self.asset).read_bytes(), original)


class LegacyThumbnailPruneTests(SimpleTestCase):
    setUp = fixtures.ThumbnailStorageTests.setUp
    thumbnail = fixtures.ThumbnailStorageTests.thumbnail

    def legacy_files(self):
        root = Path(self.directory.name) / ".thumbnails/v1"
        directory = root / str(self.asset.id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ("a" * 64 + ".jpg")).write_bytes(b"legacy derivative")
        (directory / "asset.lock").touch()
        (directory / "tmpabcdefgh.part").touch()
        slots = root / "slots"
        slots.mkdir(exist_ok=True)
        (slots / "0.lock").touch()
        (slots / "1.lock").touch()
        return root, directory

    def test_paused_prune_only_removes_legacy_and_preserves_v2_original_and_other_namespaces(self):
        legacy, _ = self.legacy_files()
        current = self.thumbnail()
        original = asset_path(self.asset).read_bytes()
        with patch.dict(os.environ, {"E5_WRITERS_PAUSED": "1"}):
            call_command("prune_legacy_thumbnail_cache")
            call_command("prune_legacy_thumbnail_cache")
        self.assertFalse(legacy.exists())
        self.assertTrue(current.exists())
        self.assertEqual(asset_path(self.asset).read_bytes(), original)
        checked = e5_media_preflight(self.directory.name)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode())

    def test_prune_requires_orchestration_pause_gate(self):
        legacy, _ = self.legacy_files()
        with patch.dict(os.environ, {"E5_WRITERS_PAUSED": "0"}):
            with self.assertRaisesMessage(CommandError, "writers must be paused"):
                call_command("prune_legacy_thumbnail_cache")
        self.assertTrue(legacy.exists())

    def test_unknown_file_symlink_and_special_node_fail_before_deleting_any_cache(self):
        for unsafe in ("unknown", "symlink", "special"):
            legacy, directory = self.legacy_files()
            node = directory / "unsafe"
            if unsafe == "unknown":
                node.write_bytes(b"unknown data")
            elif unsafe == "symlink":
                node.symlink_to(asset_path(self.asset))
            else:
                os.mkfifo(node)
            with patch.dict(os.environ, {"E5_WRITERS_PAUSED": "1"}):
                with self.assertRaisesMessage(CommandError, "Unsafe legacy thumbnail cache layout"):
                    call_command("prune_legacy_thumbnail_cache")
            self.assertTrue((directory / ("a" * 64 + ".jpg")).exists())
            self.assertTrue(asset_path(self.asset).exists())
            node.unlink()

    def test_cleanup_limit_and_io_failure_are_nonzero_without_original_path_disclosure(self):
        legacy, _ = self.legacy_files()
        with patch.dict(os.environ, {"E5_WRITERS_PAUSED": "1"}):
            with patch("catalog.management.commands.prune_legacy_thumbnail_cache.MAX_NODES", 1):
                with self.assertRaisesMessage(CommandError, "cleanup node limit"):
                    call_command("prune_legacy_thumbnail_cache")
            self.assertTrue(legacy.exists())
            with patch("catalog.management.commands.prune_legacy_thumbnail_cache.LocalStorage.sync_directory",
                       side_effect=OSError("private/original/path")):
                with self.assertRaisesMessage(CommandError, "keep E5 writers paused") as failed:
                    call_command("prune_legacy_thumbnail_cache")
            self.assertNotIn("private/original/path", str(failed.exception))
            self.assertTrue(asset_path(self.asset).exists())

    def test_empty_directory_count_is_bounded_before_any_removal(self):
        root = Path(self.directory.name) / ".thumbnails/v1"
        empty = root / str(self.asset.id)
        empty.mkdir(parents=True)
        with patch.dict(os.environ, {"E5_WRITERS_PAUSED": "1"}):
            with patch("catalog.management.commands.prune_legacy_thumbnail_cache.MAX_NODES", 1):
                with self.assertRaisesMessage(CommandError, "cleanup node limit"):
                    call_command("prune_legacy_thumbnail_cache")
        self.assertTrue(empty.exists())
