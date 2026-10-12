"""GIF demuxing receives a private seekable descriptor, including on timeout."""
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from catalog import thumbnails
from catalog.media import asset_path, gif_dimensions, jpeg_dimensions
from catalog.tests import test_media_thumbnails as thumbnail_fixtures
from catalog.validation import CatalogError


class SeekableGifThumbnailTests(SimpleTestCase):
    setUp = thumbnail_fixtures.ThumbnailStorageTests.setUp
    thumbnail = thumbnail_fixtures.ThumbnailStorageTests.thumbnail
    def install_gif(self, data):
        self.asset.kind, self.asset.content_type = "GIF", "image/gif"
        self.asset.width, self.asset.height = gif_dimensions(data)
        self.asset.byte_size = len(data)
        self.asset.sha256 = hashlib.sha256(data).hexdigest()
        asset_path(self.asset).write_bytes(data)

    def synthetic_gif(self):
        target = Path(self.directory.name) / "multi-frame.gif"
        subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-f", "lavfi", "-i",
                        "testsrc=size=96x160:rate=10:duration=2", "-threads", "1", "-loop", "0", str(target)],
                       check=True, timeout=10)
        return target.read_bytes()

    def test_gif_decoder_uses_seekable_private_descriptor_and_closes_it(self):
        data = self.synthetic_gif()
        self.install_gif(data)
        run = subprocess.run
        descriptors = []
        def checked(arguments, **options):
            self.assertTrue("input" not in options, "GIF must not use a non-seekable input pipe")
            self.assertEqual(arguments[arguments.index("-protocol_whitelist") + 1], "file")
            self.assertEqual(arguments[arguments.index("-f") + 1], "gif")
            descriptor, = options["pass_fds"]
            descriptors.append(descriptor)
            self.assertEqual(os.lseek(descriptor, 0, os.SEEK_CUR), 0)
            self.assertEqual(os.read(descriptor, len(data)), data)
            os.lseek(descriptor, 0, os.SEEK_SET)
            source = arguments[arguments.index("-i") + 1]
            self.assertIn(source, (f"/proc/self/fd/{descriptor}", f"/dev/fd/{descriptor}"))
            return run(arguments, **options)
        with patch("catalog.thumbnails.subprocess.run", side_effect=checked):
            result = self.thumbnail()
        self.assertEqual(jpeg_dimensions(result.read_bytes()), (96, 160))
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_gif_input_descriptor_is_closed_after_timeout_or_io_error(self):
        self.install_gif(self.synthetic_gif())
        factory = tempfile.TemporaryFile
        for failure in (subprocess.TimeoutExpired("decoder", 8), OSError("decoder unavailable")):
            handles = []
            def recorded(*args, **kwargs):
                handle = factory(*args, **kwargs)
                handles.append(handle)
                return handle
            with patch("catalog.thumbnails.tempfile.TemporaryFile", side_effect=recorded):
                with patch("catalog.thumbnails.subprocess.run", side_effect=failure):
                    with self.assertRaises((CatalogError, OSError)):
                        self.thumbnail()
            self.assertEqual(len(handles), 1)
            self.assertTrue(handles[0].closed)
            self.assertFalse(list(Path(self.directory.name).rglob("*.part")))

    def test_private_real_world_gif_generates_bounded_static_thumbnail(self):
        fixture = os.environ.get("PRIVATE_THUMBNAIL_GIF_FIXTURE")
        if not fixture:
            self.skipTest("Private real-world GIF is supplied outside the repository.")
        data = Path(fixture).read_bytes()
        self.install_gif(data)
        result = self.thumbnail().read_bytes()
        self.assertLessEqual(len(result), 65536)
        self.assertLessEqual(max(jpeg_dimensions(result)), 320)
        self.assertEqual(asset_path(self.asset).read_bytes(), data)

    def test_descriptor_path_selects_linux_or_macos_without_network_protocols(self):
        for platform, prefix in (("linux", "/proc/self/fd/"), ("darwin", "/dev/fd/")):
            with self.subTest(platform=platform), patch("catalog.thumbnails.sys.platform", platform):
                with thumbnails.decoder_input(b"private-verified-bytes", "gif") as (protocol, path, options):
                    descriptor, = options["pass_fds"]
                    self.assertEqual(protocol, "file")
                    self.assertEqual(path, f"{prefix}{descriptor}")
                    self.assertEqual(os.read(descriptor, 64), b"private-verified-bytes")
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
