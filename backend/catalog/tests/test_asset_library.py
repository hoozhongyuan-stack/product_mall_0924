"""E0 library permissions, bounded filters and conservative reference facts."""
from django.test import Client, TestCase
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
from io import BytesIO

from catalog.models import Asset
from catalog.media import asset_path
from catalog.tests import test_media_flow as fixtures

png = fixtures.png
playable_mp4 = fixtures.playable_mp4


class AssetLibraryTests(TestCase):
    setUp = fixtures.ProductMediaFlowTests.setUp
    json_request = fixtures.ProductMediaFlowTests.json_request
    upload = fixtures.ProductMediaFlowTests.upload
    product_payload = fixtures.ProductMediaFlowTests.product_payload
    def test_range_initial_read_failure_is_503_and_closes_source(self):
        uploaded = self.upload("VIDEO", playable_mp4(), "range.mp4", "video/mp4").json()["data"]
        class FailingSource(BytesIO):
            def read(self, size=-1):
                raise OSError("disk read failed")
        for source in (FailingSource(b"data"), BytesIO(b"short")):
            with self.subTest(source=type(source).__name__), patch("pathlib.Path.open", return_value=source):
                result = self.client.get(f"/api/v1/admin/assets/{uploaded['assetId']}/file", HTTP_RANGE="bytes=0-7")
            self.assertEqual(result.status_code, 503)
            self.assertTrue(source.closed)

    def test_private_file_storage_failure_returns_recoverable_error(self):
        uploaded = self.upload("IMAGE", png(), "disk.png", "image/png").json()["data"]
        with patch("catalog.media_views.serve_asset", side_effect=OSError("disk unavailable")):
            result = self.client.get(f"/api/v1/admin/assets/{uploaded['assetId']}/file")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["code"], "MEDIA_STORAGE_UNAVAILABLE")

    def test_library_metadata_filters_missing_and_pagination(self):
        first = self.upload("IMAGE", png(), "square.png", "image/png").json()["data"]
        wide = self.upload("IMAGE", png(2, 1), "wide.png", "image/png").json()["data"]
        result = self.client.get("/api/v1/admin/assets?kind=IMAGE&square=1&pageSize=1")
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertEqual(data["total"], 1)
        row = data["items"][0]
        self.assertEqual(row["assetId"], first["assetId"])
        self.assertEqual(row["bindingStatus"], "UNBOUND")
        self.assertEqual(row["availability"], "READY")
        self.assertEqual(len(row["sha256"]), 64)
        self.assertNotIn("storedName", row)
        self.assertNotIn("createdBy", row)
        asset_path(Asset.objects.get(id=wide["assetId"])).unlink()
        missing = self.client.get("/api/v1/admin/assets?q=wide").json()["data"]
        self.assertEqual(missing["items"][0]["availability"], "MISSING")
        self.assertIn("no-store", result["Cache-Control"])
        self.assertIn("Cookie", result["Vary"])
        for query in ("kind=SVG", "binding=INVALID", "page=0", "pageSize=101", "square=yes", "page=1&page=2", "owner=other", "q=" + "a" * 121):
            self.assertEqual(self.client.get("/api/v1/admin/assets?" + query).status_code, 400)
        self.assertEqual(Client().get("/api/v1/admin/assets").status_code, 401)

    def test_reference_list_and_bound_filter(self):
        uploaded = self.upload("IMAGE", png(), "used.png", "image/png").json()["data"]
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=uploaded["assetId"])).json()["data"]
        data = self.client.get("/api/v1/admin/assets?binding=BOUND").json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["bindingStatus"], "BOUND")
        refs = self.client.get(f"/api/v1/admin/assets/{uploaded['assetId']}/references?pageSize=1")
        self.assertEqual(refs.status_code, 200, refs.content)
        row = refs.json()["data"]["items"][0]
        self.assertEqual(row["objectId"], created["productId"])
        self.assertEqual(row["role"], "MAIN_IMAGE")
        self.assertEqual(row["domain"], "CATALOG")
        self.assertEqual(self.client.get("/api/v1/admin/assets?binding=UNBOUND").json()["data"]["total"], 0)

    def test_expired_unbound_is_unavailable_and_cannot_bind(self):
        uploaded = self.upload("IMAGE", png(), "expired.png", "image/png").json()["data"]
        Asset.objects.filter(id=uploaded["assetId"]).update(created_at=timezone.now() - timedelta(days=2))
        row = self.client.get("/api/v1/admin/assets").json()["data"]["items"][0]
        self.assertEqual(row["availability"], "EXPIRED")
        created = self.json_request("post", "/api/v1/admin/products", self.product_payload(
            mainImageAssetId=uploaded["assetId"]))
        self.assertEqual(created.status_code, 400, created.content)
