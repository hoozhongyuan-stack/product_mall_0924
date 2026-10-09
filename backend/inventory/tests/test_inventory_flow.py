import json
import uuid

from django.db import DatabaseError, IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.models import Category, Product, Sku, SkuUnitVersion
from inventory.models import InboundDocument, InventoryBalance, InventoryLedger, Warehouse
from inventory.pool_access import (PoolBindingError, bind_sku_to_pool, ensure_independent_pool,
                                   pool_info_for_skus, resolve_anchor_id)
from inventory.availability import default_available_base_units


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

    def patch(self, path, payload):
        return self.owner.patch(path, data=json.dumps(payload), content_type="application/json",
                                HTTP_X_CSRFTOKEN=self.owner.cookies["csrftoken"].value)

    def test_warehouse_status_requires_revision_and_empty_non_default_warehouse(self):
        default = self.make_warehouse()
        secondary = self.post(self.owner, "/api/v1/admin/warehouses", {
            "code": "SECONDARY", "name": "辅助仓", "isDefault": False,
        }).json()["data"]
        path = f"/api/v1/admin/warehouses/{secondary['warehouseId']}/status"
        invalid = self.patch(path, {"enabled": False, "expectedRevision": True})
        self.assertEqual(invalid.status_code, 400)
        stopped = self.patch(path, {"enabled": False, "expectedRevision": 1})
        self.assertEqual(stopped.status_code, 200, stopped.content)
        self.assertFalse(stopped.json()["data"]["enabled"])
        self.assertEqual(stopped.json()["data"]["revision"], 2)
        self.assertEqual(self.patch(path, {"enabled": True, "expectedRevision": 1}).status_code, 409)
        self.assertTrue(self.patch(path, {"enabled": True, "expectedRevision": 2}).json()["data"]["enabled"])
        default_path = f"/api/v1/admin/warehouses/{default['warehouseId']}/status"
        self.assertEqual(self.patch(default_path, {"enabled": False, "expectedRevision": 1}).status_code, 409)

    def test_warehouse_stop_rejects_stock_and_open_document(self):
        self.make_warehouse()
        secondary = self.post(self.owner, "/api/v1/admin/warehouses", {
            "code": "SECONDARY", "name": "辅助仓", "isDefault": False,
        }).json()["data"]
        path = f"/api/v1/admin/warehouses/{secondary['warehouseId']}/status"
        self.draft(secondary)
        blocked = self.patch(path, {"enabled": False, "expectedRevision": 1})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("未完成单据", blocked.json()["error"]["message"])
        stock_warehouse = self.post(self.owner, "/api/v1/admin/warehouses", {
            "code": "STOCK", "name": "有货仓", "isDefault": False,
        }).json()["data"]
        stock_path = f"/api/v1/admin/warehouses/{stock_warehouse['warehouseId']}/status"
        InventoryBalance.objects.create(warehouse_id=stock_warehouse["warehouseId"], sku=self.sku,
                                        on_hand_base_units=3)
        blocked = self.patch(stock_path, {"enabled": False, "expectedRevision": 1})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("账面库存", blocked.json()["error"]["message"])

    def test_inventory_list_filters_and_invalid_dates(self):
        warehouse = self.make_warehouse()
        draft = self.draft(warehouse)
        confirmed = self.post(self.owner,
                              f"/api/v1/admin/inventory/inbounds/{draft['inboundId']}/confirm",
                              {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        today = timezone.localdate().isoformat()
        inbound = self.owner.get(f"/api/v1/admin/inventory/inbounds?documentNo={draft['documentNo']}&dateFrom={today}&dateTo={today}")
        self.assertEqual(inbound.status_code, 200, inbound.content)
        self.assertEqual(inbound.json()["data"]["total"], 1)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/inbounds?dateFrom=2026-12-01&dateTo=2026-01-01").status_code, 400)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/outbounds?dateFrom=bad").status_code, 400)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/stocktakes?dateTo=bad").status_code, 400)
        ledger = self.owner.get(f"/api/v1/admin/inventory/ledgers?dateFrom={today}&dateTo={today}")
        self.assertEqual(ledger.status_code, 200, ledger.content)
        self.assertEqual(ledger.json()["data"]["total"], 1)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/ledgers?documentNo=bad").status_code, 400)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/balances?availability=AVAILABLE").json()["data"]["total"], 1)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/balances?availability=UNAVAILABLE").json()["data"]["total"], 0)

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

    def test_shared_pool_inbound_keeps_one_balance_and_sale_sku_provenance(self):
        warehouse = self.make_warehouse()
        first = self.draft(warehouse)
        self.post(self.owner, f"/api/v1/admin/inventory/inbounds/{first['inboundId']}/confirm",
                  {"expectedRevision": 1}, str(uuid.uuid4()))
        sibling = Sku.objects.create(product=self.sku.product, sku_code="SKU-BOX",
                                     spec_key="box", list_price_fen=2200)
        unit = SkuUnitVersion.objects.create(sku=sibling, base_unit="瓶", sale_unit="盒", ratio=3)
        sibling.current_unit = unit
        sibling.save(update_fields=["current_unit"])
        pool_id = pool_info_for_skus([self.sku.id])[self.sku.id]["poolId"]
        bind_sku_to_pool(sibling.id, uuid.UUID(pool_id), sibling.revision)
        self.assertEqual(resolve_anchor_id(sibling.id), self.sku.id)
        second = self.draft(warehouse, [{"skuId": str(sibling.id), "quantity": 1, "unit": "SALE"}])
        confirmed = self.post(self.owner,
                              f"/api/v1/admin/inventory/inbounds/{second['inboundId']}/confirm",
                              {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(InventoryBalance.objects.count(), 1)
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 15)
        self.assertEqual(InventoryLedger.objects.get(inbound_line__sku=sibling).sku_id, sibling.id)
        _, available = default_available_base_units([self.sku.id, sibling.id])
        self.assertEqual(available, {self.sku.id: 15, sibling.id: 15})
        other = Sku.objects.create(product=self.sku.product, sku_code="SKU-OTHER",
                                   spec_key="other", list_price_fen=1000)
        other_unit = SkuUnitVersion.objects.create(sku=other, base_unit="瓶", sale_unit="瓶", ratio=1)
        other.current_unit = other_unit
        other.save(update_fields=["current_unit"])
        InventoryBalance.objects.create(warehouse=Warehouse.objects.get(), sku=other,
                                        on_hand_base_units=0)
        with self.assertRaises(PoolBindingError):
            bind_sku_to_pool(other.id, uuid.UUID(pool_id), other.revision)

    def test_manual_pool_binding_api_retired_without_side_effects(self):
        sibling = Sku.objects.create(product=self.sku.product, sku_code="SKU-FIRST-ALIAS",
                                     spec_key="first-alias", list_price_fen=1200)
        unit = SkuUnitVersion.objects.create(sku=sibling, base_unit="瓶", sale_unit="提", ratio=2)
        sibling.current_unit = unit
        sibling.save(update_fields=["current_unit"])
        payload = {"skuId": str(sibling.id), "anchorSkuId": str(self.sku.id),
                   "expectedSkuRevision": sibling.revision}
        path = "/api/v1/admin/inventory/pool-bindings"
        first = self.post(self.owner, path, payload)
        self.assertEqual(first.status_code, 409, first.content)
        self.assertEqual(first.json()['error']['code'], 'POOL_BINDING_REMOVED')
        self.assertEqual(resolve_anchor_id(sibling.id), sibling.id)
        listed = self.owner.get(f"{path}?productId={self.sku.product_id}")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertTrue(all(item['poolId'] is None for item in listed.json()['data']['items']))

    def test_binding_rejects_stale_or_incompatible_sku_and_rebinds_unused_pool(self):
        anchor_pool = ensure_independent_pool(self.sku)
        other = Sku.objects.create(product=self.sku.product, sku_code="SKU-UNUSED",
                                   spec_key="unused", list_price_fen=1000)
        with self.assertRaises(PoolBindingError):
            ensure_independent_pool(other)
        unit = SkuUnitVersion.objects.create(sku=other, base_unit="瓶", sale_unit="瓶", ratio=1)
        other.current_unit = unit
        other.save(update_fields=["current_unit"])
        old_pool = ensure_independent_pool(other)
        with self.assertRaises(PoolBindingError):
            bind_sku_to_pool(other.id, anchor_pool.id, other.revision + 1)
        self.assertEqual(resolve_anchor_id(other.id), other.id)
        pool, changed = bind_sku_to_pool(other.id, anchor_pool.id, other.revision)
        self.assertTrue(changed)
        self.assertEqual(pool.id, anchor_pool.id)
        self.assertEqual(resolve_anchor_id(other.id), self.sku.id)
        self.assertFalse(type(old_pool).objects.filter(pk=old_pool.id).exists())
        another = Sku.objects.create(product=self.sku.product, sku_code="SKU-OTHER-ANCHOR",
                                     spec_key="other-anchor", list_price_fen=1000)
        other_unit = SkuUnitVersion.objects.create(sku=another, base_unit="件", sale_unit="件", ratio=1)
        another.current_unit = other_unit
        another.save(update_fields=["current_unit"])
        with self.assertRaises(PoolBindingError):
            bind_sku_to_pool(another.id, anchor_pool.id, another.revision)
        third = Sku.objects.create(product=self.sku.product, sku_code="SKU-THIRD-ANCHOR",
                                   spec_key="third-anchor", list_price_fen=1000)
        third_unit = SkuUnitVersion.objects.create(sku=third, base_unit="瓶", sale_unit="瓶", ratio=1)
        third.current_unit = third_unit
        third.save(update_fields=["current_unit"])
        with self.assertRaises(PoolBindingError):
            bind_sku_to_pool(other.id, ensure_independent_pool(third).id, other.revision)

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
        options = reader.get("/api/v1/admin/inventory/pool-product-options?keyword=SKU-1")
        self.assertEqual(options.status_code, 200, options.content)
        self.assertEqual(options.json()["data"]["items"][0]["productId"], str(self.sku.product_id))
        self.assertEqual(reader.get("/api/v1/admin/product-rows").status_code, 403)
        self.assertEqual(self.post(reader, "/api/v1/admin/warehouses", {
            "code": "X", "name": "X 仓", "isDefault": True,
        }).status_code, 403)
        warehouse = self.make_warehouse()
        self.assertEqual(reader.patch(f"/api/v1/admin/warehouses/{warehouse['warehouseId']}/status",
                                      data=json.dumps({"enabled": False, "expectedRevision": 1}),
                                      content_type="application/json",
                                      HTTP_X_CSRFTOKEN=reader.cookies["csrftoken"].value).status_code, 403)
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
