"""Product media upload, binding, public exposure and replacement contracts."""

import json
import struct
import tempfile
import zlib
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import StopUpload
from django.utils import timezone

from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.models import Asset
from catalog.media import asset_path
from catalog.upload_limit import AssetUploadLimitHandler


def png(width=1, height=1):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + b"\x66\x44\x22" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def fake_mp4_container():
    def box(kind, payload):
        return struct.pack(">I4s", len(payload) + 8, kind) + payload
    return box(b"ftyp", b"isom\x00\x00\x02\x00isommp42") + box(b"moov", b"\x00" * 16) + box(b"mdat", b"\x00" * 16)


def playable_mp4():
    return (Path(__file__).parent / "fixtures-short.mp4").read_bytes()


def corrupt_png_pixels():
    data = png()
    idat = data.index(b"IDAT")
    payload_offset = idat + 4
    payload_size = struct.unpack_from(">I", data, idat - 4)[0]
    payload = b"x" * payload_size
    crc = struct.pack(">I", zlib.crc32(b"IDAT" + payload))
    return data[:payload_offset] + payload + crc + data[payload_offset + payload_size + 4:]


class ProductMediaFlowTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        settings.enable()
        self.addCleanup(settings.disable)
        AdminAccount.objects.create_user("owner", "Safe owner passphrase 2026!",
                                         display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.client = Client(enforce_csrf_checks=True)
        self.client.get("/api/v1/admin/auth/csrf")
        login = self.json_request("post", "/api/v1/admin/auth/login", {
            "loginName": "owner", "password": "Safe owner passphrase 2026!",
        })
        self.assertEqual(login.status_code, 200, login.content)
        root = self.json_request("post", "/api/v1/admin/categories", {"name": "酒类"}).json()["data"]
        self.leaf = self.json_request("post", "/api/v1/admin/categories", {
            "name": "葡萄酒", "parentId": root["id"],
        }).json()["data"]

    def json_request(self, verb, path, payload):
        return getattr(self.client, verb)(path, data=json.dumps(payload), content_type="application/json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def upload(self, kind, data, filename, content_type):
        return self.client.post("/api/v1/admin/assets", {
            "kind": kind, "file": SimpleUploadedFile(filename, data, content_type=content_type),
        }, HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def product_payload(self, **media):
        return {"productNo": "P-MEDIA-1", "name": "媒体测试商品",
                "categoryId": self.leaf["id"], "fulfillmentKind": "SHIP", "status": "DRAFT",
                "descriptionHtml": "<p>详情</p>", "specAxes": [], "skus": [{
                    "skuCode": "SKU-MEDIA-1", "specOptionKeys": [], "listPriceFen": 1000,
                    "saleStatus": "OFF_SALE", "gradePrices": [],
                    "unit": {"baseUnit": "瓶", "saleUnit": "瓶", "ratio": 1},
                }], **media}

    def test_upload_bind_publish_and_public_files(self):
        main = self.upload("IMAGE", png(), "main.png", "image/png")
        gallery = self.upload("IMAGE", png(2, 1), "side.png", "image/png")
        self.assertEqual(main.status_code, 201, main.content)
        self.assertEqual(gallery.status_code, 201, gallery.content)
        main_data = main.json()["data"]
        side_data = gallery.json()["data"]
        self.assertEqual((main_data["width"], main_data["height"]), (1, 1))
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=main_data["assetId"], galleryAssetIds=[side_data["assetId"]],
            videoAssetId=None))
        self.assertEqual(created.status_code, 201, created.content)
        result = created.json()["data"]
        detail = self.client.get(f"/api/v1/admin/products/{result['productId']}").json()["data"]
        self.assertEqual(detail["mainImage"]["assetId"], main_data["assetId"])
        self.assertEqual(detail["galleryImages"][0]["assetId"], side_data["assetId"])
        self.assertEqual(self.client.get(f"/api/v1/app/assets/{main_data['assetId']}/file").status_code, 404)
        sku = result["skus"][0]
        self.assertEqual(self.json_request("patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"],
        }).status_code, 200)
        public = self.client.get(f"/api/v1/app/products/{result['productId']}")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertIn(f"/api/v1/app/assets/{main_data['assetId']}/file", public.json()["data"]["mainImageUrl"])
        file_response = self.client.get(f"/api/v1/app/assets/{main_data['assetId']}/file")
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(b"".join(file_response.streaming_content), png())
        self.assertEqual(file_response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(file_response["Cache-Control"], "no-store")

    def test_video_upload_edit_replace_and_remove(self):
        first = self.upload("IMAGE", png(), "first.png", "image/png")
        second = self.upload("IMAGE", png(), "second.png", "image/png")
        video = self.upload("VIDEO", playable_mp4(), "clip.mp4", "video/mp4")
        self.assertEqual(video.status_code, 201, video.content)
        asset_ids = [response.json()["data"]["assetId"] for response in (first, second, video)]
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload())
        self.assertEqual(created.status_code, 201, created.content)
        item = created.json()["data"]
        path = f"/api/v1/admin/products/{item['productId']}"
        changed = self.json_request("patch", path, {"mainImageAssetId": asset_ids[0],
            "videoAssetId": asset_ids[2], "expectedRevision": item["productRevision"]})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.json()["data"]["video"]["assetId"], asset_ids[2])
        self.assertEqual(self.client.get(f"/api/v1/admin/assets/{asset_ids[2]}/file",
            HTTP_RANGE="bytes=0-7").status_code, 206)
        replaced = self.json_request("patch", path, {"mainImageAssetId": asset_ids[1],
            "videoAssetId": None, "expectedRevision": changed.json()["data"]["productRevision"]})
        self.assertEqual(replaced.status_code, 200, replaced.content)
        self.assertEqual(replaced.json()["data"]["mainImage"]["assetId"], asset_ids[1])
        self.assertIsNone(replaced.json()["data"]["video"])
        self.assertEqual(self.client.get(f"/api/v1/app/assets/{asset_ids[2]}/file").status_code, 404)

    def test_invalid_upload_binding_and_publish_are_rejected(self):
        invalid = self.upload("IMAGE", b"<svg onload='alert(1)'>", "wrong.png", "image/png")
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertEqual(self.upload("IMAGE", corrupt_png_pixels(), "broken.png", "image/png").status_code, 400)
        self.assertEqual(self.upload("VIDEO", fake_mp4_container(), "fake.mp4", "video/mp4").status_code, 400)
        clip = playable_mp4()
        broken_late_frame = clip[:2200] + b"\x00" * 16 + clip[2216:]
        self.assertEqual(self.upload("VIDEO", broken_late_frame, "broken.mp4", "video/mp4").status_code, 400)
        with override_settings(PRODUCT_MEDIA_FFMPEG_BIN="missing-ffmpeg-for-test"):
            unavailable = self.upload("IMAGE", png(), "valid.png", "image/png")
        self.assertEqual(unavailable.status_code, 503, unavailable.content)
        nonsquare = self.upload("IMAGE", png(2, 1), "wide.png", "image/png")
        self.assertEqual(nonsquare.status_code, 201, nonsquare.content)
        wide_id = nonsquare.json()["data"]["assetId"]
        refused = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=wide_id))
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()["error"]["code"], "MEDIA_INVALID")
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload())
        self.assertEqual(created.status_code, 201, created.content)
        item = created.json()["data"]
        sku = item["skus"][0]
        publish = self.json_request("patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"],
        })
        self.assertEqual(publish.status_code, 400, publish.content)
        self.assertEqual(publish.json()["error"]["code"], "MEDIA_REQUIRED")

    def test_upload_size_and_permissions(self):
        with override_settings(PRODUCT_IMAGE_MAX_BYTES=32):
            oversized = self.upload("IMAGE", png(), "large.png", "image/png")
        self.assertEqual(oversized.status_code, 400, oversized.content)
        unauthenticated = Client(enforce_csrf_checks=True)
        unauthenticated.get("/api/v1/admin/auth/csrf")
        rejected = unauthenticated.post("/api/v1/admin/assets", {
            "kind": "IMAGE", "file": SimpleUploadedFile("image.png", png(), content_type="image/png"),
        }, HTTP_X_CSRFTOKEN=unauthenticated.cookies["csrftoken"].value)
        self.assertEqual(rejected.status_code, 401, rejected.content)
        group = PermissionGroup.objects.create(code="media_limited", name="媒体受限")
        GroupPermission.objects.create(group=group, code="catalog.write")
        staff = AdminAccount.objects.create_user("limited", "Limited test passphrase 2026!",
                                                  display_name="受限员工", kind=AdminAccount.Kind.STAFF)
        AccountGroup.objects.create(account=staff, group=group)
        limited = Client(enforce_csrf_checks=True)
        limited.get("/api/v1/admin/auth/csrf")
        login = limited.post("/api/v1/admin/auth/login", data=json.dumps({
            "loginName": "limited", "password": "Limited test passphrase 2026!",
        }), content_type="application/json", HTTP_X_CSRFTOKEN=limited.cookies["csrftoken"].value)
        self.assertEqual(login.status_code, 200, login.content)
        denied_upload = limited.post("/api/v1/admin/assets", {
            "kind": "IMAGE", "file": SimpleUploadedFile("image.png", png(), content_type="image/png"),
        }, HTTP_X_CSRFTOKEN=limited.cookies["csrftoken"].value)
        self.assertEqual(denied_upload.status_code, 403, denied_upload.content)
        uploaded = self.upload("IMAGE", png(), "allowed.png", "image/png")
        asset_id = uploaded.json()["data"]["assetId"]
        self.assertEqual(limited.get(f"/api/v1/admin/assets/{asset_id}/file").status_code, 403)
        with override_settings(PRODUCT_MEDIA_ORPHAN_USER_QUOTA_BYTES=len(png())):
            quota = self.upload("IMAGE", png(), "over-quota.png", "image/png")
        self.assertEqual(quota.status_code, 409, quota.content)

    def test_media_count_duplicate_and_on_sale_removal(self):
        image_id = self.upload("IMAGE", png(), "main.png", "image/png").json()["data"]["assetId"]
        duplicated = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=image_id, galleryAssetIds=[image_id]))
        self.assertEqual(duplicated.status_code, 400, duplicated.content)
        too_many = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            galleryAssetIds=[str(index) for index in range(9)]))
        self.assertEqual(too_many.status_code, 400, too_many.content)
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=image_id)).json()["data"]
        path = f"/api/v1/admin/products/{created['productId']}"
        sku = created["skus"][0]
        published = self.json_request("patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"]})
        self.assertEqual(published.status_code, 200, published.content)
        refused = self.json_request("patch", path, {"mainImageAssetId": None,
            "expectedRevision": published.json()["data"]["productRevision"]})
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()["error"]["code"], "MEDIA_REQUIRED")

    def test_expired_unbound_asset_is_purged_but_bound_asset_remains(self):
        unbound_id = self.upload("IMAGE", png(), "unused.png", "image/png").json()["data"]["assetId"]
        bound_id = self.upload("IMAGE", png(), "used.png", "image/png").json()["data"]["assetId"]
        self.json_request("post", "/api/v1/admin/products", self.product_payload(mainImageAssetId=bound_id))
        expired = timezone.now() - timedelta(days=2)
        Asset.objects.filter(id__in=[unbound_id, bound_id]).update(created_at=expired)
        path = asset_path(Asset.objects.get(id=unbound_id))
        with self.captureOnCommitCallbacks(execute=True):
            call_command("purge_orphan_media", verbosity=0)
        self.assertFalse(Asset.objects.filter(id=unbound_id).exists())
        self.assertFalse(path.exists())
        self.assertTrue(Asset.objects.filter(id=bound_id).exists())

    def test_stream_limit_stops_upload_without_content_length(self):
        handler = AssetUploadLimitHandler()
        with override_settings(PRODUCT_VIDEO_MAX_BYTES=4):
            self.assertEqual(handler.receive_data_chunk(b"123", 0), b"123")
            with self.assertRaises(StopUpload):
                handler.receive_data_chunk(b"45", 3)

    def test_audit_failure_rolls_back_asset_and_file(self):
        with patch("catalog.media.audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.upload("IMAGE", png(), "audit.png", "image/png")
        self.assertEqual(Asset.objects.count(), 0)
        self.assertEqual(list(Path(self.media_dir.name).rglob("*.png")), [])
