"""Draft product specification replacement through the authenticated API."""

import json
import uuid
from unittest.mock import patch

from django.db import IntegrityError
from django.test import Client, TestCase

from accounts.models import AccountGroup, AdminAccount, AuditLog, GroupPermission, PermissionGroup
from catalog.models import Product, Sku, SkuGradePrice, SkuUnitVersion


class SpecEditTests(TestCase):
    def setUp(self):
        AdminAccount.objects.create_user("owner", "Safe owner passphrase 2026!",
                                         display_name="主账号", kind=AdminAccount.Kind.OWNER)
        self.owner = self.login("owner", "Safe owner passphrase 2026!")
        root = self.post(self.owner, "/api/v1/admin/categories", {
            "name": "酒类", "sortOrder": 1, "status": "ACTIVE", "parentId": None,
        }).json()["data"]
        leaf = self.post(self.owner, "/api/v1/admin/categories", {
            "name": "白酒", "sortOrder": 1, "status": "ACTIVE", "parentId": root["id"],
        }).json()["data"]
        self.product = self.post(self.owner, "/api/v1/admin/products", {
            "productNo": "P-EDIT", "name": "规格商品", "categoryId": leaf["id"],
            "fulfillmentKind": "SHIP", "status": "DRAFT", "specAxes": [{
                "clientKey": "size", "name": "容量", "sortOrder": 1, "options": [
                    {"clientKey": "small", "value": "500ml", "sortOrder": 1},
                    {"clientKey": "large", "value": "1L", "sortOrder": 2},
                ],
            }], "skus": [self.sku("S-SMALL", ["small"]), self.sku("S-LARGE", ["large"])],
        }).json()["data"]
        self.path = f"/api/v1/admin/products/{self.product['productId']}/specs"

    def send(self, client, verb, path, payload):
        return getattr(client, verb)(path, data=json.dumps(payload), content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    def post(self, client, path, payload):
        return self.send(client, "post", path, payload)

    def login(self, name, password):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.post(client, "/api/v1/admin/auth/login", {"loginName": name, "password": password})
        self.assertEqual(result.status_code, 200, result.content)
        return client

    @staticmethod
    def sku(code, keys):
        return {"skuCode": code, "specOptionKeys": keys, "listPriceFen": 3200,
                "saleStatus": "OFF_SALE", "gradePrices": [],
                "unit": {"baseUnit": "瓶", "saleUnit": "瓶", "ratio": 1}}

    def detail(self):
        return self.owner.get(f"/api/v1/admin/products/{self.product['productId']}").json()["data"]

    def edit_payload(self):
        detail = self.detail()
        axis = detail["specAxes"][0]
        options = axis["options"]
        axes = [{"id": axis["id"], "clientKey": "size", "name": axis["name"],
                 "sortOrder": 1, "options": [
                    {"id": option["id"], "clientKey": f"opt{index}",
                     "value": option["value"], "sortOrder": index + 1}
                    for index, option in enumerate(options)]}]
        by_code = {sku["skuCode"]: sku for sku in detail["skus"]}
        skus = []
        for code, key in (("S-SMALL", "opt0"), ("S-LARGE", "opt1")):
            current = by_code[code]
            skus.append({**self.sku(code, [key]), "id": current["skuId"],
                         "expectedSkuRevision": current["skuRevision"]})
        return {"expectedRevision": detail["productRevision"], "specAxes": axes, "skus": skus}

    def preview(self, payload, client=None):
        result = self.post(client or self.owner, self.path + "/preview", payload)
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()["data"]

    def stock_sku(self, sku_id):
        warehouse = self.post(self.owner, "/api/v1/admin/warehouses", {
            "code": "STOCK", "name": "库存仓", "isDefault": True,
        })
        self.assertEqual(warehouse.status_code, 201, warehouse.content)
        draft = self.owner.post(
            "/api/v1/admin/inventory/inbounds",
            data=json.dumps({
                "warehouseId": warehouse.json()["data"]["warehouseId"],
                "reason": "采购入库", "items": [{"skuId": sku_id, "quantity": 2, "unit": "BASE"}],
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.owner.cookies["csrftoken"].value,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        item = draft.json()["data"]
        confirmed = self.owner.post(
            f"/api/v1/admin/inventory/inbounds/{item['inboundId']}/confirm",
            data=json.dumps({"expectedRevision": item["revision"]}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.owner.cookies["csrftoken"].value,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)

    def test_stocked_sku_cannot_be_removed_or_rewritten(self):
        payload = self.edit_payload()
        stocked = payload["skus"][0]
        self.stock_sku(stocked["id"])
        original_code = Sku.objects.get(id=stocked["id"]).sku_code

        removed = self.edit_payload()
        removed["skus"].pop(0)
        preview = self.post(self.owner, self.path + "/preview", removed)
        self.assertEqual(preview.status_code, 409, preview.content)
        self.assertEqual(preview.json()["error"]["code"], "SKU_INVENTORY_REFERENCED")
        self.assertIn(original_code, preview.json()["error"]["message"])
        direct_save = self.send(self.owner, "put", self.path, removed)
        self.assertEqual(direct_save.status_code, 409, direct_save.content)
        self.assertEqual(direct_save.json()["error"]["code"], "SKU_INVENTORY_REFERENCED")

        changed_code = self.edit_payload()
        changed_code["skus"][0]["skuCode"] = "S-REWRITTEN"
        code_preview = self.post(self.owner, self.path + "/preview", changed_code)
        self.assertEqual(code_preview.status_code, 409, code_preview.content)
        self.assertEqual(code_preview.json()["error"]["code"], "SKU_INVENTORY_REFERENCED")

        changed_spec = self.edit_payload()
        changed_spec["specAxes"][0]["options"][0]["value"] = "已改规格"
        spec_preview = self.post(self.owner, self.path + "/preview", changed_spec)
        self.assertEqual(spec_preview.status_code, 409, spec_preview.content)
        self.assertEqual(spec_preview.json()["error"]["code"], "SKU_INVENTORY_REFERENCED")

        changed_base = self.edit_payload()
        changed_base["skus"][0]["unit"]["baseUnit"] = "箱"
        base_preview = self.post(self.owner, self.path + "/preview", changed_base)
        self.assertEqual(base_preview.status_code, 409, base_preview.content)
        self.assertEqual(base_preview.json()["error"]["code"], "SKU_INVENTORY_REFERENCED")
        self.assertEqual(Sku.objects.get(id=stocked["id"]).sku_code, original_code)
        self.assertEqual(Product.objects.get(id=self.product["productId"]).revision, 1)

    def test_stocked_sku_rejects_base_unit_api_change_but_allows_new_sale_ratio(self):
        sku = self.edit_payload()["skus"][0]
        self.stock_sku(sku["id"])
        path = f"/api/v1/admin/skus/{sku['id']}/unit"
        rejected = self.send(self.owner, "put", path, {
            "expectedRevision": sku["expectedSkuRevision"],
            "unit": {"baseUnit": "箱", "saleUnit": "箱", "ratio": 1},
        })
        self.assertEqual(rejected.status_code, 409, rejected.content)
        self.assertEqual(rejected.json()["error"]["code"], "SKU_INVENTORY_REFERENCED")
        updated = self.send(self.owner, "put", path, {
            "expectedRevision": sku["expectedSkuRevision"],
            "unit": {"baseUnit": "瓶", "saleUnit": "箱", "ratio": 6},
        })
        self.assertEqual(updated.status_code, 200, updated.content)

    def test_stock_added_after_preview_blocks_save_and_preserves_inventory(self):
        payload = self.edit_payload()
        removed_id = payload["skus"].pop(0)["id"]
        preview = self.preview(payload)
        self.stock_sku(removed_id)
        refused = self.send(self.owner, "put", self.path, {
            **payload, "previewToken": preview["previewToken"],
        })
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(refused.json()["error"]["code"], "SKU_INVENTORY_REFERENCED")
        self.assertTrue(Sku.objects.filter(id=removed_id).exists())
        self.assertEqual(Product.objects.get(id=self.product["productId"]).revision, 1)

    def test_preview_and_save_preserve_matching_sku_and_add_combination(self):
        payload = self.edit_payload()
        original_id = payload["skus"][0]["id"]
        original_unit_id = Sku.objects.get(id=original_id).current_unit_id
        payload["specAxes"][0]["options"].append({
            "clientKey": "medium", "value": "750ml", "sortOrder": 3,
        })
        payload["skus"].append(self.sku("S-MEDIUM", ["medium"]))
        preview = self.preview(payload)
        self.assertEqual(len(preview["retained"]), 2)
        self.assertEqual([item["skuCode"] for item in preview["added"]], ["S-MEDIUM"])
        self.assertEqual(preview["removed"], [])
        saved = self.send(self.owner, "put", self.path, {**payload, "previewToken": preview["previewToken"]})
        self.assertEqual(saved.status_code, 200, saved.content)
        detail = self.detail()
        self.assertEqual(len(detail["skus"]), 3)
        self.assertEqual(next(item for item in detail["skus"] if item["skuCode"] == "S-SMALL")["skuId"], original_id)
        self.assertEqual(Sku.objects.get(id=original_id).current_unit_id, original_unit_id)
        self.assertNotIn("S-MEDIUM", [item["skuCode"] for item in payload["skus"] if "id" in item])

    def test_removed_combination_requires_preview_and_cleans_current_draft_data(self):
        payload = self.edit_payload()
        removed_id = payload["skus"].pop()["id"]
        grade = self.owner.get("/api/v1/admin/member-grades").json()["data"][0]
        SkuGradePrice.objects.create(sku_id=removed_id, grade_id=grade["id"], price_fen=2800)
        preview = self.preview(payload)
        self.assertEqual(preview["removed"][0]["skuId"], removed_id)
        self.assertEqual(preview["removed"][0]["unitVersionCount"], 1)
        self.assertEqual(preview["removed"][0]["gradePriceCount"], 1)
        self.assertEqual(preview["removed"][0]["skuCode"], "S-LARGE")
        self.assertEqual(self.send(self.owner, "put", self.path, payload).status_code, 409)
        saved = self.send(self.owner, "put", self.path, {**payload, "previewToken": preview["previewToken"]})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertFalse(Sku.objects.filter(id=removed_id).exists())
        self.assertFalse(SkuUnitVersion.objects.filter(sku_id=removed_id).exists())
        self.assertFalse(SkuGradePrice.objects.filter(sku_id=removed_id).exists())
        audit = AuditLog.objects.get(action_code="product.specs.update")
        self.assertEqual(audit.after["removed"][0]["skuId"], removed_id)
        self.assertEqual(audit.after["removed"][0]["gradePriceCount"], 1)

    def test_duplicate_conflict_and_failure_leave_original_data(self):
        payload = self.edit_payload()
        payload["skus"][1]["specOptionKeys"] = ["opt0"]
        invalid = self.post(self.owner, self.path + "/preview", payload)
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertEqual(Sku.objects.filter(product_id=self.product["productId"]).count(), 2)
        payload = self.edit_payload()
        payload["skus"][1]["skuCode"] = "s-small"
        duplicate_code = self.post(self.owner, self.path + "/preview", payload)
        self.assertEqual(duplicate_code.status_code, 400, duplicate_code.content)
        self.assertEqual(Sku.objects.filter(product_id=self.product["productId"]).count(), 2)
        payload = self.edit_payload()
        preview = self.preview(payload)
        stale = self.send(self.owner, "put", self.path, {**payload, "expectedRevision": 999,
            "previewToken": preview["previewToken"]})
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(self.detail()["productRevision"], 1)

    def test_composite_permissions_are_required(self):
        payload = self.edit_payload()
        preview = self.preview(payload)
        for index, missing in enumerate(("catalog.write", "sku.price.write", "sku.status.write", "sku.unit.write")):
            with self.subTest(missing=missing):
                codes = {"catalog.read", "catalog.write", "sku.price.write", "sku.status.write", "sku.unit.write"} - {missing}
                group = PermissionGroup.objects.create(code=f"spec_{index}", name=f"规格权限 {index}")
                GroupPermission.objects.bulk_create([GroupPermission(group=group, code=code) for code in codes])
                account = AdminAccount.objects.create_user(f"staff{index}", "Safe staff passphrase 2026!",
                    display_name=f"staff{index}", kind=AdminAccount.Kind.STAFF)
                AccountGroup.objects.create(account=account, group=group)
                client = self.login(f"staff{index}", "Safe staff passphrase 2026!")
                denied_preview = self.post(client, self.path + "/preview", payload)
                self.assertEqual(denied_preview.status_code, 403, denied_preview.content)
                denied = self.send(client, "put", self.path, {**payload, "previewToken": preview["previewToken"]})
                self.assertEqual(denied.status_code, 403, denied.content)

    def test_sku_code_is_unique_across_products(self):
        other = self.post(self.owner, "/api/v1/admin/products", {
            "productNo": "P-OTHER", "name": "其他商品", "categoryId": self.detail()["categoryId"],
            "fulfillmentKind": "SHIP", "status": "DRAFT",
            "specAxes": [{"clientKey": "flavour", "name": "口味", "sortOrder": 1,
                          "options": [{"clientKey": "classic", "value": "经典", "sortOrder": 1}]}],
            "skus": [self.sku("CROSS-PRODUCT", ["classic"])],
        })
        self.assertEqual(other.status_code, 201, other.content)
        payload = self.edit_payload()
        payload["skus"][1]["skuCode"] = "cross-product"
        refused = self.post(self.owner, self.path + "/preview", payload)
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(refused.json()["error"]["code"], "SKU_CODE_DUPLICATE")
        self.assertEqual(Sku.objects.filter(product_id=self.product["productId"]).count(), 2)

    def test_preview_becomes_stale_after_independent_sku_update(self):
        payload = self.edit_payload()
        payload["skus"].pop()
        preview = self.preview(payload)
        removed = preview["removed"][0]
        changed = self.send(self.owner, "patch", f"/api/v1/admin/skus/{removed['skuId']}/price", {
            "expectedRevision": 1, "listPriceFen": 4500,
        })
        self.assertEqual(changed.status_code, 200, changed.content)
        attempted = self.send(self.owner, "put", self.path, {**payload,
            "previewToken": preview["previewToken"]})
        self.assertEqual(attempted.status_code, 409, attempted.content)
        self.assertTrue(Sku.objects.filter(id=removed["skuId"]).exists())
        self.assertEqual(Product.objects.get(id=self.product["productId"]).revision, 1)

    def test_failure_mid_save_rolls_back_deleted_sku_and_option(self):
        payload = self.edit_payload()
        removed_id = payload["skus"].pop()["id"]
        payload["specAxes"][0]["options"].pop()
        payload["specAxes"][0]["options"].append({
            "clientKey": "medium", "value": "750ml", "sortOrder": 2,
        })
        payload["skus"].append(self.sku("S-MEDIUM", ["medium"]))
        preview = self.preview(payload)
        with patch("catalog.spec_edit.SkuUnitVersion.objects.create", side_effect=IntegrityError("injected")):
            attempted = self.send(self.owner, "put", self.path, {**payload,
                "previewToken": preview["previewToken"]})
        self.assertEqual(attempted.status_code, 409, attempted.content)
        self.assertTrue(Sku.objects.filter(id=removed_id).exists())
        self.assertEqual(len(self.detail()["specAxes"][0]["options"]), 2)
        self.assertFalse(Sku.objects.filter(sku_code="S-MEDIUM").exists())

    def test_existing_grade_price_and_unit_version_are_preserved(self):
        original = self.edit_payload()["skus"][0]
        sku = Sku.objects.get(id=original["id"])
        grade = self.owner.get("/api/v1/admin/member-grades").json()["data"][0]
        SkuGradePrice.objects.create(sku=sku, grade_id=grade["id"], price_fen=2700)
        payload = self.edit_payload()
        payload["specAxes"][0]["name"] = "净含量"
        payload["skus"][0]["gradePrices"] = [{"gradeId": grade["id"], "priceFen": 2700}]
        old_unit_id = sku.current_unit_id
        old_price_id = SkuGradePrice.objects.get(sku=sku, active=True).id
        preview = self.preview(payload)
        saved = self.send(self.owner, "put", self.path, {**payload,
            "previewToken": preview["previewToken"]})
        self.assertEqual(saved.status_code, 200, saved.content)
        sku.refresh_from_db()
        self.assertEqual(str(sku.id), original["id"])
        self.assertEqual(sku.current_unit_id, old_unit_id)
        self.assertEqual(SkuGradePrice.objects.get(sku=sku, active=True).id, old_price_id)

    def test_non_draft_product_cannot_change_specs(self):
        Product.objects.filter(id=self.product["productId"]).update(
            status=Product.Status.OFF_SALE, ever_on_sale=True)
        refused = self.post(self.owner, self.path + "/preview", self.edit_payload())
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(refused.json()["error"]["code"], "PRODUCT_NOT_DRAFT")

    def test_sku_codes_can_swap_without_identity_change(self):
        payload = self.edit_payload()
        first, second = payload["skus"]
        first["skuCode"], second["skuCode"] = second["skuCode"], first["skuCode"]
        preview = self.preview(payload)
        saved = self.send(self.owner, "put", self.path, {**payload,
            "previewToken": preview["previewToken"]})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(Sku.objects.get(id=first["id"]).sku_code, "S-LARGE")
        self.assertEqual(Sku.objects.get(id=second["id"]).sku_code, "S-SMALL")
        repeated = self.send(self.owner, "put", self.path, {**payload,
            "previewToken": preview["previewToken"]})
        self.assertEqual(repeated.status_code, 409, repeated.content)
        self.assertEqual(Sku.objects.filter(product_id=self.product["productId"]).count(), 2)
