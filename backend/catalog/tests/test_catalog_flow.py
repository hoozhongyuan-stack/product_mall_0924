"""Stage A catalog contracts exercised through authenticated and public APIs."""

import json
import tempfile
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.models import MemberGrade, Product, Sku, SkuGradePrice

from .test_media_flow import png


OWNER_PASSWORD = "Safe owner passphrase 2026!"
STAFF_PASSWORD = "Safe staff passphrase 2026!"


class CatalogFlowTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media_dir.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        AdminAccount.objects.create_user(
            "owner", OWNER_PASSWORD, display_name="主账号", kind=AdminAccount.Kind.OWNER
        )
        self.owner = self.authenticated_client("owner", OWNER_PASSWORD)

    def send(self, client, verb, path, payload):
        return getattr(client, verb)(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )

    def send_with_key(self, client, path, payload, key):
        return client.post(path, data=json.dumps(payload), content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value, HTTP_IDEMPOTENCY_KEY=key)

    def authenticated_client(self, name, password):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.send(client, "post", "/api/v1/admin/auth/login", {
            "loginName": name, "password": password,
        })
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def staff_client(self, name, codes):
        group = PermissionGroup.objects.create(code=f"group_{name}", name=f"权限组 {name}")
        GroupPermission.objects.bulk_create(
            [GroupPermission(group=group, code=code) for code in codes]
        )
        account = AdminAccount.objects.create_user(
            name, STAFF_PASSWORD, display_name=name, kind=AdminAccount.Kind.STAFF
        )
        AccountGroup.objects.create(account=account, group=group)
        return account, self.authenticated_client(name, STAFF_PASSWORD)

    def create_category(self, name, parent_id=None, status="ACTIVE", client=None):
        result = self.send(client or self.owner, "post", "/api/v1/admin/categories", {
            "parentId": parent_id, "name": name, "sortOrder": 10, "status": status,
        })
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()["data"]

    def leaf(self):
        root = self.create_category("酒类")
        return self.create_category("白酒", root["id"])

    def product_payload(self, category_id, product_no="P-001", sku_code="S-001"):
        return {
            "productNo": product_no,
            "name": "测试商品",
            "categoryId": category_id,
            "fulfillmentKind": "SHIP",
            "status": "DRAFT",
            "descriptionHtml": "<p>商品说明</p>",
            "mainImageAssetId": None,
            "galleryAssetIds": [],
            "specAxes": [],
            "skus": [{
                "skuCode": sku_code,
                "specOptionKeys": [],
                "listPriceFen": 3200,
                "saleStatus": "OFF_SALE",
                "gradePrices": [],
                "unit": {"baseUnit": "瓶", "saleUnit": "瓶", "ratio": 1},
            }],
        }

    def create_product(self, payload, client=None):
        result = self.send(client or self.owner, "post", "/api/v1/admin/products", payload)
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()["data"]

    def attach_main_for_publish(self, created):
        uploaded = self.owner.post("/api/v1/admin/assets", {
            "kind": "IMAGE", "file": SimpleUploadedFile("main.png", png(), content_type="image/png"),
        }, HTTP_X_CSRFTOKEN=self.owner.cookies["csrftoken"].value)
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        Product.objects.filter(id=created["productId"]).update(main_image_id=uploaded.json()["data"]["assetId"])

    def test_category_depth_and_inactive_parent_are_rejected(self):
        root = self.create_category("酒类")
        leaf = self.create_category("白酒", root["id"])
        third = self.send(self.owner, "post", "/api/v1/admin/categories", {
            "parentId": leaf["id"], "name": "酱香", "sortOrder": 10, "status": "ACTIVE",
        })
        self.assertIn(third.status_code, (400, 422), third.content)

        empty_root = self.create_category("待停用分类")
        disabled = self.send(self.owner, "patch", f"/api/v1/admin/categories/{empty_root['id']}", {
            "status": "INACTIVE", "expectedRevision": empty_root["revision"],
        })
        self.assertEqual(disabled.status_code, 200, disabled.content)
        child = self.send(self.owner, "post", "/api/v1/admin/categories", {
            "parentId": empty_root["id"], "name": "新子类", "sortOrder": 20, "status": "ACTIVE",
        })
        self.assertIn(child.status_code, (400, 409, 422), child.content)

    def test_category_with_on_sale_product_cannot_be_disabled(self):
        leaf = self.leaf()
        created = self.create_product(self.product_payload(leaf["id"]))
        self.attach_main_for_publish(created)
        sku = created["skus"][0]
        published = self.send(self.owner, "patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"],
        })
        self.assertEqual(published.status_code, 200, published.content)
        refused = self.send(self.owner, "patch", f"/api/v1/admin/categories/{leaf['id']}", {
            "status": "INACTIVE", "expectedRevision": leaf["revision"],
        })
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(refused.json()["error"]["code"], "CATEGORY_IN_USE")

    def test_category_directory_reports_related_product_counts(self):
        leaf = self.leaf()
        self.create_product(self.product_payload(leaf["id"]))
        result = self.owner.get("/api/v1/admin/categories")
        self.assertEqual(result.status_code, 200, result.content)
        rows = {row["id"]: row for row in result.json()["data"]}
        self.assertEqual(rows[leaf["id"]]["productCount"], 1)
        self.assertEqual(rows[leaf["parentId"]]["productCount"], 1)
        self.assertEqual(rows[leaf["id"]]["onSaleProductCount"], 0)

    def test_multi_spec_product_creates_distinct_sku_rows(self):
        leaf = self.leaf()
        payload = self.product_payload(leaf["id"])
        payload["specAxes"] = [
            {"clientKey": "size", "name": "容量", "sortOrder": 1, "options": [
                {"clientKey": "small", "value": "500ml", "sortOrder": 1},
                {"clientKey": "large", "value": "1L", "sortOrder": 2},
            ]},
            {"clientKey": "pack", "name": "包装", "sortOrder": 2, "options": [
                {"clientKey": "single", "value": "单瓶", "sortOrder": 1},
            ]},
        ]
        payload["skus"] = [
            {**payload["skus"][0], "skuCode": "S-SMALL",
             "specOptionKeys": ["small", "single"]},
            {**payload["skus"][0], "skuCode": "S-LARGE",
             "specOptionKeys": ["large", "single"]},
        ]
        created = self.create_product(payload)
        self.assertEqual(len(created["skus"]), 2)
        listed = self.owner.get("/api/v1/admin/sku-rows?page=1&pageSize=20")
        self.assertEqual(listed.status_code, 200, listed.content)
        data = listed.json()["data"]
        self.assertEqual((data["page"], data["pageSize"], data["total"]), (1, 20, 2))
        rows = {row["skuCode"]: row for row in data["rows"]}
        self.assertEqual(rows["S-SMALL"]["specs"], [
            {"name": "容量", "value": "500ml"}, {"name": "包装", "value": "单瓶"},
        ])
        self.assertEqual(rows["S-LARGE"]["specs"][0]["value"], "1L")
        self.assertEqual(rows["S-SMALL"]["rowKey"], rows["S-SMALL"]["skuId"])
        self.assertEqual(rows["S-SMALL"]["productNo"], "P-001")
        self.assertEqual(rows["S-SMALL"]["listPriceFen"], 3200)

    def test_duplicate_spec_combination_rolls_back_entire_product(self):
        leaf = self.leaf()
        payload = self.product_payload(leaf["id"])
        payload["specAxes"] = [{"clientKey": "size", "name": "容量", "sortOrder": 1,
            "options": [{"clientKey": "small", "value": "500ml", "sortOrder": 1}]}]
        payload["skus"] = [
            {**payload["skus"][0], "specOptionKeys": ["small"]},
            {**payload["skus"][0], "skuCode": "S-002", "specOptionKeys": ["small"]},
        ]
        failed = self.send(self.owner, "post", "/api/v1/admin/products", payload)
        self.assertIn(failed.status_code, (400, 409, 422), failed.content)
        payload["skus"] = payload["skus"][:1]
        self.create_product(payload)  # The first request must not reserve either code.

    def test_product_and_sku_codes_are_unique_case_insensitively(self):
        leaf = self.leaf()
        self.create_product(self.product_payload(leaf["id"], "P-001", "S-001"))
        duplicate_product = self.send(self.owner, "post", "/api/v1/admin/products",
            self.product_payload(leaf["id"], "p-001", "S-002"))
        self.assertEqual(duplicate_product.status_code, 409, duplicate_product.content)
        duplicate_sku = self.send(self.owner, "post", "/api/v1/admin/products",
            self.product_payload(leaf["id"], "P-002", "s-001"))
        self.assertEqual(duplicate_sku.status_code, 409, duplicate_sku.content)
        self.assertEqual(duplicate_sku.json()["error"]["code"], "SKU_CODE_DUPLICATE")

    def test_composite_product_create_requires_each_sku_write_permission(self):
        leaf = self.leaf()
        for index, missing in enumerate(("sku.price.write", "sku.status.write", "sku.unit.write")):
            with self.subTest(missing=missing):
                codes = {"catalog.read", "catalog.write", "sku.price.write",
                         "sku.status.write", "sku.unit.write"} - {missing}
                _, client = self.staff_client(missing.replace(".", "_"), codes)
                attempted = self.send(client, "post", "/api/v1/admin/products",
                    self.product_payload(leaf["id"], f"P-ALLOW-{index}", f"S-ALLOW-{index}"))
                self.assertEqual(attempted.status_code, 403, attempted.content)
                self.assertEqual(attempted.json()["error"]["code"], "PERMISSION_DENIED")

    def test_sku_status_permission_and_revision(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        self.attach_main_for_publish(created)
        sku = created["skus"][0]
        _, reader = self.staff_client("status_reader", {"catalog.read"})
        path = f"/api/v1/admin/skus/{sku['skuId']}/status"
        change = {"saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"]}
        self.assertEqual(self.send(reader, "patch", path, change).status_code, 403)
        _, writer = self.staff_client("status_writer", {"catalog.read", "sku.status.write"})
        updated = self.send(writer, "patch", path, change)
        self.assertEqual(updated.status_code, 200, updated.content)
        stale = self.send(writer, "patch", path, change)
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(stale.json()["error"]["code"], "REVISION_CONFLICT")

    def test_grade_price_permission_and_revision(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        sku = created["skus"][0]
        grades = self.owner.get("/api/v1/admin/member-grades")
        self.assertEqual(grades.status_code, 200, grades.content)
        grade_id = grades.json()["data"][0]["id"]
        path = f"/api/v1/admin/skus/{sku['skuId']}/grade-prices"
        change = {"gradePrices": [{"gradeId": grade_id, "priceFen": 2800}],
                  "expectedRevision": sku["skuRevision"]}
        _, reader = self.staff_client("price_reader", {"catalog.read"})
        self.assertEqual(self.send(reader, "put", path, change).status_code, 403)
        _, writer = self.staff_client("price_writer", {"catalog.read", "sku.price.write"})
        updated = self.send(writer, "put", path, change)
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(self.send(writer, "put", path, change).status_code, 409)

    def test_grade_price_update_preserves_disabled_grade_price(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        sku = created["skus"][0]
        grades = list(MemberGrade.objects.order_by("rank")[:2])
        path = f"/api/v1/admin/skus/{sku['skuId']}/grade-prices"
        first = self.send(self.owner, "put", path, {
            "gradePrices": [
                {"gradeId": str(grades[0].id), "priceFen": 2800},
                {"gradeId": str(grades[1].id), "priceFen": 2600},
            ], "expectedRevision": sku["skuRevision"],
        })
        self.assertEqual(first.status_code, 200, first.content)
        grades[1].enabled = False
        grades[1].save(update_fields=["enabled"])
        second = self.send(self.owner, "put", path, {
            "gradePrices": [{"gradeId": str(grades[0].id), "priceFen": 2700}],
            "expectedRevision": first.json()["data"]["skuRevision"],
        })
        self.assertEqual(second.status_code, 200, second.content)
        retained = SkuGradePrice.objects.get(sku_id=sku["skuId"], grade=grades[1], active=True)
        self.assertEqual(retained.price_fen, 2600)

    def test_batch_sku_status_reports_success_and_stale_item(self):
        leaf = self.leaf()
        first_product = self.create_product(self.product_payload(leaf["id"]))
        second_product = self.create_product(self.product_payload(leaf["id"], "P-002", "S-002"))
        self.attach_main_for_publish(first_product)
        first, second = first_product["skus"][0], second_product["skus"][0]
        path = "/api/v1/admin/sku-rows/batch-status"
        payload = {"saleStatus": "ON_SALE", "items": [
            {"skuId": first["skuId"], "expectedRevision": first["skuRevision"]},
            {"skuId": second["skuId"], "expectedRevision": second["skuRevision"] + 1},
        ]}
        result = self.send(self.owner, "post", path, payload)
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertEqual((data["successCount"], data["failedCount"]), (1, 1))
        self.assertEqual([(row["id"], row["success"]) for row in data["results"]], [
            (first["skuId"], True), (second["skuId"], False),
        ])
        self.assertEqual(data["results"][1]["code"], "REVISION_CONFLICT")
        listed = self.owner.get("/api/v1/admin/sku-rows")
        rows = {row["skuId"]: row for row in listed.json()["data"]["rows"]}
        self.assertEqual(rows[first["skuId"]]["saleStatus"], "ON_SALE")
        self.assertEqual(rows[second["skuId"]]["saleStatus"], "OFF_SALE")
        self.assertEqual(rows[first["skuId"]]["productStatus"], "ON_SALE")
        self.assertEqual(rows[second["skuId"]]["productStatus"], "DRAFT")
        _, reader = self.staff_client("batch_reader", {"catalog.read"})
        self.assertEqual(self.send(reader, "post", path, payload).status_code, 403)

    def test_batch_category_updates_each_product_once_and_checks_revision(self):
        source = self.leaf()
        target = self.create_category("其他", source["parentId"])
        first = self.create_product(self.product_payload(source["id"]))
        second = self.create_product(self.product_payload(source["id"], "P-002", "S-002"))
        path = "/api/v1/admin/products/batch-category"
        preview = self.send(self.owner, "post", f"{path}/preview", {
            "categoryId": target["id"], "skuIds": [first["skus"][0]["skuId"], second["skus"][0]["skuId"]],
        })
        self.assertEqual(preview.status_code, 200, preview.content)
        changed = self.send(self.owner, "patch", f"/api/v1/admin/products/{second['productId']}", {
            "name": "商品已被改动", "expectedRevision": second["productRevision"],
        })
        self.assertEqual(changed.status_code, 200, changed.content)
        request = {"categoryId": target["id"], "previewToken": preview.json()["data"]["previewToken"], "items": [
            {"productId": first["productId"], "expectedRevision": first["productRevision"]},
            {"productId": second["productId"], "expectedRevision": second["productRevision"]},
        ]}
        result = self.send_with_key(self.owner, path, request, "16338e95-e27c-4810-81a8-94ddbf4c60b5")
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertEqual((data["successCount"], data["failedCount"]), (1, 1))
        self.assertEqual(data["results"][1]["code"], "REVISION_CONFLICT")
        self.assertEqual(self.owner.get(f"/api/v1/admin/products/{first['productId']}").json()["data"]["categoryId"], target["id"])
        self.assertEqual(self.owner.get(f"/api/v1/admin/products/{second['productId']}").json()["data"]["categoryId"], source["id"])
        repeated = self.send(self.owner, "post", path, {"categoryId": target["id"], "items": [
            {"productId": first["productId"], "expectedRevision": first["productRevision"]},
            {"productId": first["productId"], "expectedRevision": first["productRevision"]},
        ]})
        self.assertEqual(repeated.status_code, 400, repeated.content)
        _, reader = self.staff_client("category_reader", {"catalog.read"})
        self.assertEqual(self.send(reader, "post", path, {"categoryId": target["id"], "items": [
            {"productId": second["productId"], "expectedRevision": second["productRevision"]},
        ]}).status_code, 403)

    def test_batch_category_preview_counts_other_skus_and_retry_is_idempotent(self):
        leaf = self.leaf()
        target = self.create_category("其他", leaf["parentId"])
        payload = self.product_payload(leaf["id"])
        payload["specAxes"] = [{"clientKey": "pack", "name": "包装", "sortOrder": 1,
            "options": [{"clientKey": "one", "value": "单瓶", "sortOrder": 1},
                        {"clientKey": "six", "value": "整箱", "sortOrder": 2}]}]
        payload["skus"] = [
            {**payload["skus"][0], "specOptionKeys": ["one"]},
            {**payload["skus"][0], "skuCode": "S-002", "specOptionKeys": ["six"]},
        ]
        product = self.create_product(payload)
        preview = self.send(self.owner, "post", "/api/v1/admin/products/batch-category/preview", {
            "skuIds": [product["skus"][0]["skuId"]], "categoryId": target["id"],
        })
        self.assertEqual(preview.status_code, 200, preview.content)
        summary = preview.json()["data"]
        self.assertEqual((summary["productCount"], summary["skuCount"], summary["otherSkuCount"]), (1, 2, 1))
        self.assertEqual(summary["items"][0]["selectedSkuCount"], 1)
        self.assertTrue(summary["items"][0]["canChange"])
        request = {"categoryId": target["id"], "previewToken": summary["previewToken"],
                   "items": [{"productId": product["productId"],
                              "expectedRevision": product["productRevision"]}]}
        path = "/api/v1/admin/products/batch-category"
        key = "4e5b259b-9cb4-4fde-8ea3-a00e0cc8a3bc"
        first = self.send_with_key(self.owner, path, request, key)
        self.assertEqual(first.status_code, 200, first.content)
        second = self.send_with_key(self.owner, path, request, key)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json()["data"], second.json()["data"])
        updated = self.owner.get(f"/api/v1/admin/products/{product['productId']}").json()["data"]
        self.assertEqual(updated["categoryId"], target["id"])
        self.assertEqual(updated["productRevision"], product["productRevision"] + 1)
        collision = self.send_with_key(self.owner, path, {**request, "categoryId": leaf["id"]}, key)
        self.assertEqual(collision.status_code, 409, collision.content)

    def test_sku_rows_filter_fulfillment_and_expose_batch_revisions(self):
        leaf = self.leaf()
        ship = self.create_product(self.product_payload(leaf["id"]))
        redeem_payload = self.product_payload(leaf["id"], "P-002", "S-002")
        redeem_payload["fulfillmentKind"] = "REDEEM"
        self.create_product(redeem_payload)
        result = self.owner.get("/api/v1/admin/sku-rows?fulfillmentKind=SHIP")
        self.assertEqual(result.status_code, 200, result.content)
        rows = result.json()["data"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["productId"], ship["productId"])
        self.assertEqual(rows[0]["productRevision"], ship["productRevision"])
        self.assertEqual(rows[0]["fulfillmentKind"], "SHIP")
        invalid = self.owner.get("/api/v1/admin/sku-rows?fulfillmentKind=UNKNOWN")
        self.assertEqual(invalid.status_code, 400, invalid.content)

    def test_batch_actions_reject_invalid_selection_and_inactive_target(self):
        leaf = self.leaf()
        product = self.create_product(self.product_payload(leaf["id"]))
        sku = product["skus"][0]
        row = {"skuId": sku["skuId"], "expectedRevision": sku["skuRevision"]}
        path = "/api/v1/admin/sku-rows/batch-status"
        duplicate = self.send(self.owner, "post", path, {
            "saleStatus": "ON_SALE", "items": [row, row],
        })
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        too_many = self.send(self.owner, "post", path, {
            "saleStatus": "ON_SALE", "items": [row] * 101,
        })
        self.assertEqual(too_many.status_code, 400, too_many.content)
        self.assertEqual(self.owner.get("/api/v1/admin/sku-rows").json()["data"]["rows"][0]["saleStatus"], "OFF_SALE")

        inactive = self.create_category("未启用", leaf["parentId"], status="INACTIVE")
        target = self.send(self.owner, "post", "/api/v1/admin/products/batch-category/preview", {
            "categoryId": inactive["id"], "skuIds": [sku["skuId"]],
        })
        self.assertEqual(target.status_code, 200, target.content)
        self.assertFalse(target.json()["data"]["items"][0]["canChange"])
        self.assertEqual(self.owner.get(f"/api/v1/admin/products/{product['productId']}").json()["data"]["categoryId"], leaf["id"])

    def test_daily_price_permission_and_revision(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        sku = created["skus"][0]
        path = f"/api/v1/admin/skus/{sku['skuId']}/price"
        change = {"listPriceFen": 3900, "expectedRevision": sku["skuRevision"]}
        _, reader = self.staff_client("daily_reader", {"catalog.read"})
        self.assertEqual(self.send(reader, "patch", path, change).status_code, 403)
        _, writer = self.staff_client("daily_writer", {"catalog.read", "sku.price.write"})
        updated = self.send(writer, "patch", path, change)
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["data"]["listPriceFen"], 3900)
        self.assertEqual(self.send(writer, "patch", path, change).status_code, 409)

    def test_unit_change_permission_and_revision(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        sku = created["skus"][0]
        path = f"/api/v1/admin/skus/{sku['skuId']}/unit"
        change = {"unit": {"baseUnit": "瓶", "saleUnit": "箱", "ratio": 6},
                  "expectedRevision": sku["skuRevision"]}
        _, reader = self.staff_client("unit_reader", {"catalog.read"})
        self.assertEqual(self.send(reader, "put", path, change).status_code, 403)
        _, writer = self.staff_client("unit_writer", {"catalog.read", "sku.unit.write"})
        self.assertEqual(self.send(writer, "put", path, change).status_code, 400)
        change["unit"] = {"baseUnit": "件", "saleUnit": "件", "ratio": 1}
        updated = self.send(writer, "put", path, change)
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(self.send(writer, "put", path, change).status_code, 409)

    def test_disabled_account_cannot_use_catalog_session(self):
        self.leaf()
        account, client = self.staff_client("disabled_reader", {"catalog.read"})
        self.assertEqual(client.get("/api/v1/admin/categories").status_code, 200)
        account.enabled = False
        account.auth_version += 1
        account.save(update_fields=["enabled", "auth_version"])
        self.assertEqual(client.get("/api/v1/admin/categories").status_code, 401)
        self.assertEqual(self.send(client, "post", "/api/v1/admin/categories", {
            "parentId": None, "name": "越权类目", "sortOrder": 1, "status": "ACTIVE",
        }).status_code, 401)

    def test_public_product_remains_not_purchasable_without_inventory(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        self.attach_main_for_publish(created)
        sku = created["skus"][0]
        enabled = self.send(self.owner, "patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"],
        })
        self.assertEqual(enabled.status_code, 200, enabled.content)
        result = Client().get(f"/api/v1/app/products/{created['productId']}")
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertFalse(data["purchasable"])
        self.assertEqual(data["availabilityCode"], "STOCK_NOT_READY")
        self.assertNotIn("availableStock", data)

    def test_product_status_patch_is_rejected(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        _, writer = self.staff_client("base_writer", {"catalog.write", "catalog.read"})
        changed = self.send(writer, "patch", f"/api/v1/admin/products/{created['productId']}", {
            "status": "ON_SALE", "expectedRevision": created["productRevision"],
        })
        self.assertEqual(changed.status_code, 400, changed.content)

    def test_new_draft_cannot_include_an_on_sale_sku(self):
        payload = self.product_payload(self.leaf()["id"])
        payload["skus"][0]["saleStatus"] = "ON_SALE"
        result = self.send(self.owner, "post", "/api/v1/admin/products", payload)
        self.assertEqual(result.status_code, 400, result.content)
        self.assertEqual(Product.objects.count(), 0)

    def test_sale_state_and_audit_roll_back_together(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        self.attach_main_for_publish(created)
        sku = created["skus"][0]
        with patch("catalog.sale_state.audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.send(self.owner, "patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
                    "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"]})
        product = Product.objects.get(id=created["productId"])
        self.assertEqual((product.status, product.revision), (Product.Status.DRAFT, 1))
        self.assertEqual(Sku.objects.get(id=sku["skuId"]).sale_status, Sku.SaleStatus.OFF_SALE)

    def test_database_rejects_sku_and_product_sale_mismatch(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        sku = created["skus"][0]
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Sku.objects.filter(id=sku["skuId"]).update(sale_status=Sku.SaleStatus.ON_SALE)
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS sku_product_sale_guard IMMEDIATE")
        self.assertEqual(Sku.objects.get(id=sku["skuId"]).sale_status, Sku.SaleStatus.OFF_SALE)

    def test_sku_status_drives_product_through_first_and_last_sale(self):
        leaf = self.leaf()
        payload = self.product_payload(leaf["id"])
        payload["specAxes"] = [{"clientKey": "size", "name": "容量", "sortOrder": 1,
            "options": [{"clientKey": "small", "value": "小", "sortOrder": 1},
                        {"clientKey": "large", "value": "大", "sortOrder": 2}]}]
        payload["skus"] = [{**payload["skus"][0], "specOptionKeys": ["small"]},
                           {**payload["skus"][0], "skuCode": "S-002", "specOptionKeys": ["large"]}]
        created = self.create_product(payload)
        self.attach_main_for_publish(created)
        first, second = created["skus"]
        path = lambda row: f"/api/v1/admin/skus/{row['skuId']}/status"

        first_on = self.send(self.owner, "patch", path(first), {
            "saleStatus": "ON_SALE", "expectedRevision": first["skuRevision"]})
        self.assertEqual(first_on.status_code, 200, first_on.content)
        product = Product.objects.get(id=created["productId"])
        self.assertEqual(product.status, Product.Status.ON_SALE)
        self.assertTrue(product.ever_on_sale)
        self.assertEqual(Client().get("/api/v1/app/products").json()["data"]["total"], 1)

        second_on = self.send(self.owner, "patch", path(second), {
            "saleStatus": "ON_SALE", "expectedRevision": second["skuRevision"]})
        self.assertEqual(second_on.status_code, 200, second_on.content)
        first_off = self.send(self.owner, "patch", path(first), {
            "saleStatus": "OFF_SALE", "expectedRevision": first_on.json()["data"]["skuRevision"]})
        self.assertEqual(first_off.status_code, 200, first_off.content)
        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_SALE)
        second_off = self.send(self.owner, "patch", path(second), {
            "saleStatus": "OFF_SALE", "expectedRevision": second_on.json()["data"]["skuRevision"]})
        self.assertEqual(second_off.status_code, 200, second_off.content)
        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.OFF_SALE)
        self.assertTrue(product.ever_on_sale)
        self.assertEqual(Client().get("/api/v1/app/products").json()["data"]["total"], 0)

    def test_sku_on_sale_requires_product_main_image_and_preserves_draft_on_failure(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        sku = created["skus"][0]
        result = self.send(self.owner, "patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"]})
        self.assertEqual(result.status_code, 400, result.content)
        self.assertEqual(result.json()["error"]["code"], "MEDIA_REQUIRED")
        product = Product.objects.get(id=created["productId"])
        self.assertEqual(product.status, Product.Status.DRAFT)
        self.assertFalse(product.skus.filter(sale_status="ON_SALE").exists())

    def test_public_detail_does_not_expose_member_prices(self):
        leaf = self.leaf()
        grade_id = self.owner.get("/api/v1/admin/member-grades").json()["data"][0]["id"]
        payload = self.product_payload(leaf["id"])
        payload["skus"][0]["gradePrices"] = [{"gradeId": grade_id, "priceFen": 2800}]
        created = self.create_product(payload)
        self.attach_main_for_publish(created)
        sku = created["skus"][0]
        self.send(self.owner, "patch", f"/api/v1/admin/skus/{sku['skuId']}/status", {
            "saleStatus": "ON_SALE", "expectedRevision": sku["skuRevision"],
        })
        public = Client().get(f"/api/v1/app/products/{created['productId']}")
        self.assertEqual(public.status_code, 200, public.content)
        self.assertEqual(public.json()["data"]["skus"][0]["listPriceFen"], 3200)
        self.assertNotIn("gradePrices", json.dumps(public.json()["data"]))

    def test_public_detail_of_product_without_on_sale_sku_is_unavailable(self):
        created = self.create_product(self.product_payload(self.leaf()["id"]))
        self.attach_main_for_publish(created)
        public = Client().get(f"/api/v1/app/products/{created['productId']}")
        self.assertEqual(public.status_code, 404, public.content)
        self.assertEqual(public.json()["error"]["code"], "NOT_FOUND")

    def test_description_html_is_sanitized_before_public_read(self):
        payload = self.product_payload(self.leaf()["id"])
        payload["descriptionHtml"] = '<p class="x">安全描述</p><script>alert(1)</script>'
        created = self.create_product(payload)
        detail = self.owner.get(f"/api/v1/admin/products/{created['productId']}").json()["data"]
        self.assertEqual(detail["descriptionHtml"], "<p>安全描述</p>")

    def test_hundred_sku_limit_and_overflow(self):
        payload = self.product_payload(self.leaf()["id"])
        axes = []
        for axis_index in range(2):
            axes.append({"clientKey": f"axis-{axis_index}", "name": f"规格{axis_index}",
                         "sortOrder": axis_index, "options": [
                             {"clientKey": f"option-{axis_index}-{value}", "value": f"值{value}",
                              "sortOrder": value} for value in range(10)]})
        payload["specAxes"] = axes
        payload["skus"] = [{**payload["skus"][0], "skuCode": f"SKU-{a}-{b}",
                            "specOptionKeys": [f"option-0-{a}", f"option-1-{b}"]}
                           for a in range(10) for b in range(10)]
        self.assertEqual(len(self.create_product(payload)["skus"]), 100)
        payload["productNo"] = "P-002"
        payload["skus"].append({**payload["skus"][0], "skuCode": "SKU-EXTRA"})
        failed = self.send(self.owner, "post", "/api/v1/admin/products", payload)
        self.assertEqual(failed.status_code, 400, failed.content)

    def test_unknown_media_input_is_rejected_instead_of_discarded(self):
        payload = self.product_payload(self.leaf()["id"])
        payload["mainImageAssetId"] = "00000000-0000-0000-0000-000000000001"
        failed = self.send(self.owner, "post", "/api/v1/admin/products", payload)
        self.assertEqual(failed.status_code, 400, failed.content)
        self.assertEqual(failed.json()["error"]["code"], "MEDIA_INVALID")

    def test_redeem_validity_is_explicit_and_required_before_sale(self):
        from datetime import timedelta
        from django.utils import timezone
        leaf=self.leaf()
        body=self.product_payload(leaf['id'],'VALID-P','VALID-S')
        body['fulfillmentKind']='REDEEM'
        date=(timezone.localdate()+timedelta(days=30)).isoformat()
        body['redeemValidUntil']=date
        created=self.create_product(body)
        detail=self.owner.get(f"/api/v1/admin/products/{created['productId']}").json()['data']
        self.assertEqual(detail['redeemValidUntil'],date)
        self.attach_main_for_publish(created)
        sku=created['skus'][0]
        changed=self.send(self.owner,'patch',f"/api/v1/admin/skus/{sku['skuId']}/status",
                          {'saleStatus':'ON_SALE','expectedRevision':sku['skuRevision']})
        self.assertEqual(changed.status_code,200,changed.content)
        public=self.owner.get(f"/api/v1/app/products/{created['productId']}")
        self.assertEqual(public.json()['data']['redeemValidUntil'],date)
        expired=self.send(self.owner,'patch',f"/api/v1/admin/products/{created['productId']}",
            {'expectedRevision':detail['productRevision']+1,'redeemValidUntil':(timezone.localdate()-timedelta(days=1)).isoformat()})
        self.assertEqual(expired.status_code,400)

    def test_redeem_cannot_go_on_sale_without_visible_validity(self):
        leaf=self.leaf();body=self.product_payload(leaf['id'],'NOVAL-P','NOVAL-S')
        body['fulfillmentKind']='REDEEM'
        created=self.create_product(body);self.attach_main_for_publish(created)
        sku=created['skus'][0]
        result=self.send(self.owner,'patch',f"/api/v1/admin/skus/{sku['skuId']}/status",
                         {'saleStatus':'ON_SALE','expectedRevision':sku['skuRevision']})
        self.assertEqual(result.status_code,400)
