"""Private bounded thumbnails do not change original files or media records."""
import hashlib
import io
import os
import json
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase, override_settings

from accounts.models import AdminAccount
from catalog.media import asset_path, jpeg_dimensions
from catalog.models import Asset
from catalog.presentation import asset_data
from catalog.validation import CatalogError
from catalog.tests import test_media_flow as fixtures


class ThumbnailStorageTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=self.directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.asset = Asset(kind="IMAGE", content_type="image/png", width=1024, height=768,
                           stored_name="media/product/thumbnail.png")
        self.original = fixtures.png(1024, 768)
        self.asset.byte_size = len(self.original)
        self.asset.sha256 = hashlib.sha256(self.original).hexdigest()
        path = asset_path(self.asset)
        path.parent.mkdir(parents=True)
        path.write_bytes(self.original)

    def thumbnail(self):
        from catalog.thumbnails import thumbnail_path
        return thumbnail_path(self.asset)

    def test_static_output_is_bounded_private_and_original_is_unchanged(self):
        path = self.thumbnail()
        data = path.read_bytes()
        width, height = jpeg_dimensions(data)
        self.assertLessEqual(max(width, height), 320)
        self.assertLessEqual(len(data), 65536)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(asset_path(self.asset).read_bytes(), self.original)
        self.assertEqual(self.thumbnail(), path)
        self.assertEqual(asset_data(self.asset)["thumbnailUrl"],
                         f"/api/v1/admin/assets/{self.asset.id}/thumbnail")
        self.asset.kind = "VIDEO"
        self.assertNotIn("thumbnailUrl", asset_data(self.asset))

    def test_concurrent_requests_generate_once_and_publish_complete_file(self):
        from catalog import thumbnails
        with patch("catalog.thumbnails.generate_thumbnail", wraps=thumbnails.generate_thumbnail) as generate:
            with ThreadPoolExecutor(max_workers=6) as workers:
                paths = list(workers.map(lambda _: self.thumbnail(), range(6)))
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(len(set(paths)), 1)
        self.assertTrue(all(path.read_bytes().endswith(b"\xff\xd9") for path in paths))
        self.assertFalse(list(Path(self.directory.name).rglob("*.part")))

    def test_separate_workers_share_atomic_cache_and_only_one_decoder(self):
        worker = """
import django,json,sys
from pathlib import Path
from unittest.mock import patch
django.setup()
from django.conf import settings
settings.MEDIA_ROOT=sys.argv[1]
from catalog.models import Asset
from catalog import thumbnails
asset=Asset(**json.loads(sys.argv[2]))
generate=thumbnails.generate_thumbnail
def recorded(*args):
    with (Path(sys.argv[1])/'decoder-count').open('a') as stream:
        stream.write('generated\\n')
    return generate(*args)
with patch('catalog.thumbnails.generate_thumbnail',side_effect=recorded):
    path=thumbnails.thumbnail_path(asset)
    print(path.stat().st_size)
"""
        values = {field: getattr(self.asset, field) for field in
                  ("kind", "content_type", "byte_size", "width", "height", "sha256", "stored_name")}
        values["id"] = str(self.asset.id)
        processes = [subprocess.Popen([sys.executable, "-c", worker, self.directory.name, json.dumps(values)],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        try:
            for process in processes:
                output, diagnostics = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, diagnostics.decode())
                self.assertTrue(0 < int(output) <= 65536)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.assertEqual((Path(self.directory.name) / "decoder-count").read_text(), "generated\n")

    def test_missing_source_does_not_serve_preexisting_cache(self):
        self.thumbnail()
        asset_path(self.asset).unlink()
        with self.assertRaises(CatalogError) as failure:
            self.thumbnail()
        self.assertEqual((failure.exception.code, failure.exception.status), ("NOT_FOUND", 404))

    def test_changed_source_invalidates_cache_without_changing_asset(self):
        first = self.thumbnail()
        source = asset_path(self.asset)
        source.write_bytes(fixtures.png(640, 480))
        with patch("catalog.thumbnails.generate_thumbnail") as generate:
            with self.assertRaises(CatalogError):
                self.thumbnail()
        generate.assert_not_called()
        self.assertTrue(first.exists())

    def test_corrupt_source_leaves_no_partial_thumbnail(self):
        source = fixtures.corrupt_png_pixels()
        asset_path(self.asset).write_bytes(source)
        self.asset.byte_size = len(source)
        self.asset.sha256 = hashlib.sha256(source).hexdigest()
        self.asset.width = self.asset.height = 1
        with self.assertRaises(CatalogError) as failure:
            self.thumbnail()
        self.assertEqual(failure.exception.code, "MEDIA_INVALID")
        self.assertFalse(list(Path(self.directory.name).rglob("*.jpg")))
        self.assertFalse(list(Path(self.directory.name).rglob("*.part")))

    def test_unavailable_decoder_and_timeout_are_recoverable(self):
        with override_settings(PRODUCT_MEDIA_FFMPEG_BIN="nonexistent-thumbnail-decoder"):
            with self.assertRaises(CatalogError) as failure:
                self.thumbnail()
        self.assertEqual(failure.exception.status, 503)
        with patch("catalog.thumbnails.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 8)):
            with self.assertRaises(CatalogError) as failure:
                self.thumbnail()
        self.assertEqual(failure.exception.status, 503)

    def test_declared_oversized_dimensions_and_source_reject_before_decode(self):
        for field, value in (("width", 9000), ("byte_size", 11 * 1024 * 1024)):
            before = getattr(self.asset, field)
            setattr(self.asset, field, value)
            with patch("catalog.thumbnails.generate_thumbnail") as generate:
                with self.assertRaises(CatalogError):
                    self.thumbnail()
            generate.assert_not_called()
            setattr(self.asset, field, before)

    def test_gif_is_first_frame_static_jpeg(self):
        source = asset_path(self.asset)
        gif = source.with_suffix(".gif")
        subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-filter_complex_threads", "1",
                        "-f", "lavfi", "-i", "color=red:size=64x32:rate=2:duration=0.5",
                        "-f", "lavfi", "-i", "color=blue:size=64x32:rate=2:duration=0.5",
                        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-frames:v", "2", str(gif)], check=True)
        data = gif.read_bytes()
        self.asset.kind, self.asset.content_type = "GIF", "image/gif"
        self.asset.stored_name = "media/product/thumbnail.gif"
        self.asset.width, self.asset.height = 64, 32
        self.asset.byte_size, self.asset.sha256 = len(data), hashlib.sha256(data).hexdigest()
        result = self.thumbnail().read_bytes()
        self.assertEqual(jpeg_dimensions(result), (64, 32))
        self.assertFalse(result.startswith(b"GIF"))
        decoded = subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-i", "pipe:0",
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                                 input=result, stdout=subprocess.PIPE, check=True).stdout
        self.assertGreater(decoded[0], 200)
        self.assertLess(decoded[2], 40)

    def test_high_detail_large_image_has_small_real_output(self):
        def chunk(kind, payload):
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
        side = 1024
        pixels = b"".join(b"\x00" + os.urandom(side * 3) for _ in range(side))
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        data += chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
        asset_path(self.asset).write_bytes(data)
        self.asset.width = self.asset.height = side
        self.asset.byte_size, self.asset.sha256 = len(data), hashlib.sha256(data).hexdigest()
        result = self.thumbnail().read_bytes()
        self.assertGreater(len(data), 3 * 1024 * 1024)
        self.assertLessEqual(len(result), 65536)
        self.assertEqual(jpeg_dimensions(result), (320, 320))
        # Native 320px noise must also work: downscaling larger noise alone
        # conceals high-frequency output that can exceed the byte budget.
        side = 320
        pixels = b"".join(b"\x00" + os.urandom(side * 3) for _ in range(side))
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        data += chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
        asset_path(self.asset).write_bytes(data)
        self.asset.width = self.asset.height = side
        self.asset.byte_size, self.asset.sha256 = len(data), hashlib.sha256(data).hexdigest()
        self.assertLessEqual(len(self.thumbnail().read_bytes()), 65536)

    def test_actual_process_timeout_and_generation_slots_are_bounded(self):
        from catalog.thumbnails import VERSION, file_lock
        from catalog.storage import LocalStorage
        decoder = Path(self.directory.name) / "slow-decoder"
        decoder.write_text("#!/bin/sh\nexec sleep 12\n")
        decoder.chmod(0o700)
        started = time.monotonic()
        with override_settings(PRODUCT_MEDIA_FFMPEG_BIN=str(decoder)):
            with self.assertRaises(CatalogError) as failed:
                self.thumbnail()
        self.assertEqual(failed.exception.status, 503)
        self.assertLess(time.monotonic() - started, 10)
        storage = LocalStorage()
        first = storage.prepare_parent(f".thumbnails/{VERSION}/slots/0.lock")
        second = storage.prepare_parent(f".thumbnails/{VERSION}/slots/1.lock")
        with file_lock(first), file_lock(second):
            with self.assertRaises(CatalogError) as failed:
                self.thumbnail()
        self.assertEqual(failed.exception.status, 503)
        self.assertFalse(list(Path(self.directory.name).rglob("*.part")))

    def test_cache_and_original_symlinks_are_rejected(self):
        external = Path(self.directory.name) / "outside"
        external.mkdir()
        (Path(self.directory.name) / ".thumbnails").symlink_to(external, target_is_directory=True)
        with self.assertRaises(CatalogError):
            self.thumbnail()
        source = asset_path(self.asset)
        source.unlink()
        source.symlink_to(external / "private.png")
        with self.assertRaises(CatalogError):
            self.thumbnail()


class ThumbnailAuthorizationTests(TestCase):
    setUp = fixtures.ProductMediaFlowTests.setUp
    upload = fixtures.ProductMediaFlowTests.upload
    json_request = fixtures.ProductMediaFlowTests.json_request
    product_payload = fixtures.ProductMediaFlowTests.product_payload

    def test_thumbnail_route_requires_same_live_permission_as_original_even_when_cached(self):
        uploaded = self.upload("IMAGE", fixtures.png(), "small.png", "image/png").json()["data"]
        url = f"/api/v1/admin/assets/{uploaded['assetId']}/thumbnail"
        self.assertEqual(Client().get(url).status_code, 401)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("Cookie", response["Vary"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertLessEqual(len(b"".join(response.streaming_content)), 65536)
        AdminAccount.objects.create_user("reader", "Reader password 2026!", kind=AdminAccount.Kind.STAFF)
        self.json_request("post", "/api/v1/admin/auth/logout", {})
        self.json_request("post", "/api/v1/admin/auth/login", {
            "loginName": "reader", "password": "Reader password 2026!"})
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(uploaded["adminUrl"]).status_code, 403)
        self.assertEqual(Client().get(url.replace("/admin/", "/app/")).status_code, 404)

    def test_media_and_product_dtos_have_thumbnail_and_warming_is_read_only_bounded(self):
        uploaded = self.upload("IMAGE", fixtures.png(), "small.png", "image/png").json()["data"]
        url = f"/api/v1/admin/assets/{uploaded['assetId']}/thumbnail"
        self.assertEqual(uploaded["thumbnailUrl"], url)
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=uploaded["assetId"]))
        product_id = created.json()["data"]["productId"]
        detail = self.client.get(f"/api/v1/admin/products/{product_id}").json()["data"]
        self.assertEqual(detail["mainImage"]["thumbnailUrl"], url)
        before = list(Asset.objects.values())
        out = io.StringIO()
        call_command("warm_asset_thumbnails", limit=1, stdout=out)
        self.assertIn("generated=1", out.getvalue())
        self.assertEqual(list(Asset.objects.values()), before)
        for limit in (0, 201):
            with self.assertRaises(CommandError):
                call_command("warm_asset_thumbnails", limit=limit)

    def test_public_cached_thumbnail_is_rechecked_after_product_unpublish(self):
        uploaded = self.upload("IMAGE", fixtures.png(), "published.png", "image/png").json()["data"]
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=uploaded["assetId"])).json()["data"]
        sku = created["skus"][0]
        self.assertEqual(self.json_request("patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"]}).status_code, 200)
        url = f"/api/v1/app/assets/{uploaded['assetId']}/thumbnail"
        public = Client().get(url)
        self.assertEqual(public.status_code, 200)
        self.assertTrue(b"".join(public.streaming_content))
        from catalog.models import Product
        product = Product.objects.get(pk=created["productId"])
        self.assertEqual(self.json_request("patch", f"/api/v1/admin/products/{product.id}/sale-status", {
            "saleStatus": "OFF_SALE", "expectedRevision": product.revision}).status_code, 200)
        self.assertEqual(Client().get(url).status_code, 404)
        private = self.client.get(uploaded["thumbnailUrl"])
        self.assertEqual(private.status_code, 200)
        self.assertTrue(b"".join(private.streaming_content))

    def test_product_cards_and_exchange_offers_expose_public_thumb_without_changing_original_url(self):
        uploaded = self.upload("IMAGE", fixtures.png(), "card.png", "image/png").json()["data"]
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=uploaded["assetId"])).json()["data"]
        sku = created["skus"][0]
        self.json_request("patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"]})
        original = f"/api/v1/app/assets/{uploaded['assetId']}/file"
        thumbnail = original.replace("/file", "/thumbnail")
        props = {"source": "MANUAL", "productIds": [created["productId"]], "categoryId": "", "limit": 10,
                 "layout": "GRID", "sort": "NEWEST"}
        preview = self.json_request("post", "/api/v1/admin/pages/product-preview", {"props": props})
        card = preview.json()["data"]["products"][0]
        self.assertEqual((card["imageUrl"], card["thumbnailUrl"]), (original, thumbnail))
        from points_exchange.models import ExchangeOffer
        from points_exchange.operations import offer_rows
        offer = ExchangeOffer.objects.create(sku_id=sku["skuId"], points_price=100)
        offer.refresh_from_db()
        row = offer_rows([offer])[0]
        self.assertEqual((row["imageUrl"], row["thumbnailUrl"]), (original, thumbnail))

    def test_warm_failures_are_nonzero_and_do_not_leak_original_storage_paths(self):
        uploaded = self.upload("IMAGE", fixtures.png(), "deleted.png", "image/png").json()["data"]
        asset_path(Asset.objects.get(pk=uploaded["assetId"])).unlink()
        out, errors = io.StringIO(), io.StringIO()
        with self.assertRaises(CommandError):
            call_command("warm_asset_thumbnails", limit=1, stdout=out, stderr=errors)
        self.assertIn("generated=0 failed=1", out.getvalue())
        self.assertIn("NOT_FOUND", errors.getvalue())
        self.assertNotIn(self.media_dir.name, errors.getvalue())

    def test_admin_authority_is_revalidated_after_decoder_wait(self):
        from catalog import thumbnails
        uploaded = self.upload("IMAGE", fixtures.png(), "wait.png", "image/png").json()["data"]
        generate = thumbnails.generate_thumbnail
        def revoke_after_generate(*args):
            generate(*args)
            AdminAccount.objects.filter(login_name="owner").update(kind=AdminAccount.Kind.STAFF)
        with patch("catalog.thumbnails.generate_thumbnail", side_effect=revoke_after_generate):
            denied = self.client.get(uploaded["thumbnailUrl"])
        self.assertEqual(denied.status_code, 403)

    def test_io_failure_is_controlled_without_path_disclosure(self):
        uploaded = self.upload("IMAGE", fixtures.png(), "disk.png", "image/png").json()["data"]
        with patch("catalog.thumbnails.subprocess.run", side_effect=OSError(self.media_dir.name)):
            failed = self.client.get(uploaded["thumbnailUrl"])
        self.assertEqual(failed.status_code, 503)
        self.assertNotIn(self.media_dir.name, failed.content.decode())

    def test_deleting_unbound_original_removes_derived_cache(self):
        from catalog.thumbnails import thumbnail_path
        uploaded = self.upload("IMAGE", fixtures.png(), "delete-cache.png", "image/png").json()["data"]
        asset = Asset.objects.get(pk=uploaded["assetId"])
        derivative = thumbnail_path(asset)
        with self.captureOnCommitCallbacks(execute=True):
            deleted = self.client.delete(f"/api/v1/admin/assets/{asset.id}",
                HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(derivative.exists())
        self.assertFalse(asset_path(asset).exists())

    def test_public_visibility_is_revalidated_after_generation(self):
        from catalog import thumbnails
        from catalog.models import Category
        uploaded = self.upload("IMAGE", fixtures.png(), "category.png", "image/png").json()["data"]
        Category.objects.filter(pk=self.leaf["id"]).update(banner_id=uploaded["assetId"])
        generate = thumbnails.generate_thumbnail
        def unpublish_after_generate(*args):
            generate(*args)
            Category.objects.filter(pk=self.leaf["id"]).update(banner=None)
        with patch("catalog.thumbnails.generate_thumbnail", side_effect=unpublish_after_generate):
            denied = Client().get(uploaded["thumbnailUrl"].replace("/admin/", "/app/"))
        self.assertEqual(denied.status_code, 404)
