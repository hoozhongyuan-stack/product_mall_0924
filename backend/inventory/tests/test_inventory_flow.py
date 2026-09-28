import json
import uuid

from django.db import DatabaseError, IntegrityError, transaction
from django.test import Client, TestCase

from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.models import Category, Product, Sku, SkuUnitVersion
from inventory.models import InboundDocument, InventoryBalance, InventoryLedger, Warehouse


PASSWORD = "Safe owner passphrase 2026!"


class InventoryFlowTests(TestCase):
    def setUp(self):
        AdminAccount.objects.create_user("owner", PASSWORD, display_name="主账号", kind="OWNER")
        self.owner = self.login("owner")
        category = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=category)
        product = Product.objects.create(product_no="P-1", name="测试商品", category=leaf,
                                         fulfillment_kind="SHIP")
        self.sku = Sku.objects.create(product=product, sku_code="SKU-1", spec_key="single",
                                      list_price_fen=1000)
        unit = SkuUnitVersion.objects.create(sku=self.sku, base_unit="瓶", sale_unit="箱", ratio=6)
        self.sku.current_unit = unit
        self.sku.save(update_fields=["current_unit"])

    def login(self, name):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.post(client, "/api/v1/admin/auth/login", {"loginName": name, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def post(self, client, path, payload, key=None):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def make_warehouse(self):
        result = self.post(self.owner, "/api/v1/admin/warehouses", {
            "code": "MAIN", "name": "中心仓", "isDefault": True,
        })
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()["data"]

    def draft(self, warehouse, items=None, key=None):
        result = self.post(self.owner, "/api/v1/admin/inventory/inbounds", {
            "warehouseId": warehouse["warehouseId"], "reason": "采购入库",
            "items": items or [{"skuId": str(self.sku.id), "quantity": 2, "unit": "SALE"}],
        }, key or str(uuid.uuid4()))
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()["data"]

    def test_draft_confirm_and_replay_are_atomic(self):
        warehouse = self.make_warehouse()
        draft = self.draft(warehouse)
        self.assertEqual(draft["status"], "DRAFT")
        self.assertEqual(draft["items"][0]["baseQuantity"], 12)
        self.assertFalse(InventoryBalance.objects.exists())
        self.assertFalse(InventoryLedger.objects.exists())
        key = str(uuid.uuid4())
        path = f"/api/v1/admin/inventory/inbounds/{draft['inboundId']}/confirm"
        confirmed = self.post(self.owner, path, {"expectedRevision": 1}, key)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["data"]["status"], "CONFIRMED")
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 12)
        self.assertEqual(InventoryLedger.objects.get().delta_base_units, 12)
        replay = self.post(self.owner, path, {"expectedRevision": 1}, key)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(InventoryLedger.objects.count(), 1)
        changed_key = self.post(self.owner, path, {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(changed_key.status_code, 409)

    def test_draft_replay_does_not_create_second_confirmable_slip(self):
        warehouse = self.make_warehouse()
        key = str(uuid.uuid4())
        first = self.draft(warehouse, key=key)
        again = self.draft(warehouse, key=key)
        self.assertEqual(first["inboundId"], again["inboundId"])
        self.assertEqual(InboundDocument.objects.count(), 1)
        changed = self.post(self.owner, "/api/v1/admin/inventory/inbounds", {
            "warehouseId": warehouse["warehouseId"], "reason": "采购入库",
            "items": [{"skuId": str(self.sku.id), "quantity": 3, "unit": "SALE"}],
        }, key)
        self.assertEqual(changed.status_code, 409, changed.content)
        self.assertEqual(changed.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_changed_unit_rejects_entire_confirmation(self):
        warehouse = self.make_warehouse()
        draft = self.draft(warehouse)
        newer = SkuUnitVersion.objects.create(sku=self.sku, base_unit="瓶", sale_unit="箱", ratio=12)
        self.sku.current_unit = newer
        self.sku.save(update_fields=["current_unit"])
        result = self.post(self.owner,
                           f"/api/v1/admin/inventory/inbounds/{draft['inboundId']}/confirm",
                           {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(result.status_code, 409, result.content)
        self.assertFalse(InventoryBalance.objects.exists())
        self.assertFalse(InventoryLedger.objects.exists())
        self.assertEqual(InboundDocument.objects.get().status, "DRAFT")

    def test_second_line_failure_rolls_back_first_line_and_audit(self):
        warehouse = self.make_warehouse()
        other = Sku.objects.create(product=self.sku.product, sku_code="SKU-2",
                                   spec_key="other", list_price_fen=1000)
        version = SkuUnitVersion.objects.create(sku=other, base_unit="瓶", sale_unit="箱", ratio=6)
        other.current_unit = version
        other.save(update_fields=["current_unit"])
        draft = self.draft(warehouse, [
            {"skuId": str(self.sku.id), "quantity": 1, "unit": "BASE"},
            {"skuId": str(other.id), "quantity": 1, "unit": "SALE"},
        ])
        newer = SkuUnitVersion.objects.create(sku=other, base_unit="瓶", sale_unit="箱", ratio=12)
        other.current_unit = newer
        other.save(update_fields=["current_unit"])
        failed = self.post(self.owner,
                           f"/api/v1/admin/inventory/inbounds/{draft['inboundId']}/confirm",
                           {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(failed.status_code, 409, failed.content)
        self.assertFalse(InventoryBalance.objects.exists())
        self.assertFalse(InventoryLedger.objects.exists())

    def test_ledger_cannot_be_updated_or_deleted(self):
        warehouse = self.make_warehouse()
        draft = self.draft(warehouse)
        confirmed = self.post(self.owner,
                              f"/api/v1/admin/inventory/inbounds/{draft['inboundId']}/confirm",
                              {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                InventoryLedger.objects.update(reason="changed")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                InventoryLedger.objects.all().delete()
        self.assertEqual(InventoryLedger.objects.count(), 1)

    def test_permissions_default_and_balance_constraints(self):
        group = PermissionGroup.objects.create(code="inventory_test", name="库存测试")
        GroupPermission.objects.create(group=group, code="inventory.read")
        staff = AdminAccount.objects.create_user("reader", PASSWORD, display_name="查看员", kind="STAFF")
        AccountGroup.objects.create(account=staff, group=group)
        reader = self.login("reader")
        self.assertEqual(reader.get("/api/v1/admin/warehouses").status_code, 200)
        self.assertEqual(self.post(reader, "/api/v1/admin/warehouses", {
            "code": "X", "name": "X 仓", "isDefault": True,
        }).status_code, 403)
        warehouse = self.make_warehouse()
        second = self.post(self.owner, "/api/v1/admin/warehouses", {
            "code": "SECOND", "name": "次仓", "isDefault": True,
        })
        self.assertEqual(second.status_code, 409)
        row = Warehouse.objects.get(id=warehouse["warehouseId"])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InventoryBalance.objects.create(warehouse=row, sku=self.sku,
                                                on_hand_base_units=0, reserved_base_units=1)

    def test_stock_query_is_paginated_and_does_not_expose_price(self):
        warehouse = self.make_warehouse()
        draft = self.draft(warehouse)
        self.post(self.owner, f"/api/v1/admin/inventory/inbounds/{draft['inboundId']}/confirm",
                  {"expectedRevision": 1}, str(uuid.uuid4()))
        result = self.owner.get("/api/v1/admin/inventory/balances?page=1&pageSize=10")
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["availableBaseUnits"], 12)
        self.assertNotIn("price", json.dumps(data).lower())

    def test_invalid_identifiers_and_quantities_return_validation_errors(self):
        warehouse = self.make_warehouse()
        invalid = self.post(self.owner, "/api/v1/admin/inventory/inbounds", {
            "warehouseId": warehouse["warehouseId"], "reason": "采购",
            "items": [{"skuId": "bad-id", "quantity": 1, "unit": "BASE"}],
        }, str(uuid.uuid4()))
        self.assertEqual(invalid.status_code, 400, invalid.content)
        fractional = self.post(self.owner, "/api/v1/admin/inventory/inbounds", {
            "warehouseId": warehouse["warehouseId"], "reason": "采购",
            "items": [{"skuId": str(self.sku.id), "quantity": 1.5, "unit": "BASE"}],
        }, str(uuid.uuid4()))
        self.assertEqual(fractional.status_code, 400, fractional.content)
        invalid_filter = self.owner.get("/api/v1/admin/inventory/balances?warehouseId=bad-id")
        self.assertEqual(invalid_filter.status_code, 400, invalid_filter.content)
