import json
import uuid

from django.db import DatabaseError, IntegrityError, transaction
from django.test import Client, TestCase

from accounts.models import AccountGroup, AdminAccount, AuditLog, GroupPermission, PermissionGroup
from catalog.models import Category, Product, Sku, SkuUnitVersion
from inventory.models import InventoryBalance, InventoryLedger, OutboundDocument, OutboundLine, Warehouse
from inventory.sku_guards import referenced_sku_ids


PASSWORD = "Safe owner passphrase 2026!"


class OutboundFlowTests(TestCase):
    def setUp(self):
        self.actor = AdminAccount.objects.create_user("owner", PASSWORD, display_name="主账号", kind="OWNER")
        self.owner = self.login("owner")
        root = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=root)
        product = Product.objects.create(product_no="OUT-P", name="出库测试商品",
                                         category=leaf, fulfillment_kind="SHIP")
        self.sku = self.make_sku(product, "SKU-A", uuid.UUID(int=1))
        self.other = self.make_sku(product, "SKU-B", uuid.UUID(int=2))
        self.warehouse = Warehouse.objects.create(code="MAIN", name="中心仓", is_default=True)

    def make_sku(self, product, code, identifier):
        sku = Sku.objects.create(id=identifier, product=product, sku_code=code,
                                 spec_key=code, list_price_fen=1000)
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="瓶", sale_unit="箱", ratio=6)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        return sku

    def login(self, name):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.post(client, "/api/v1/admin/auth/login",
                           {"loginName": name, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def post(self, client, path, payload, key=None):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def stock(self, sku=None, on_hand=12, reserved=0):
        return InventoryBalance.objects.create(warehouse=self.warehouse, sku=sku or self.sku,
                                               on_hand_base_units=on_hand,
                                               reserved_base_units=reserved)

    def draft(self, items=None, key=None, note="损坏两箱"):
        return self.post(self.owner, "/api/v1/admin/inventory/outbounds", {
            "warehouseId": str(self.warehouse.id), "reason": "DAMAGE", "note": note,
            "items": items or [{"skuId": str(self.sku.id), "quantity": 1, "unit": "SALE"}],
        }, key or str(uuid.uuid4()))

    def confirm(self, draft, key=None, revision=1, client=None):
        return self.post(client or self.owner,
                         f"/api/v1/admin/inventory/outbounds/{draft['outboundId']}/confirm",
                         {"expectedRevision": revision}, key or str(uuid.uuid4()))

    def test_draft_confirm_replay_and_historical_ledger(self):
        self.stock(on_hand=18, reserved=4)
        created = self.draft()
        self.assertEqual(created.status_code, 201, created.content)
        draft = created.json()["data"]
        self.assertEqual(draft["items"][0]["baseQuantity"], 6)
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 18)
        self.assertFalse(InventoryLedger.objects.exists())
        key = str(uuid.uuid4())
        confirmed = self.confirm(draft, key)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["data"]["status"], "CONFIRMED")
        balance = InventoryBalance.objects.get()
        self.assertEqual((balance.on_hand_base_units, balance.reserved_base_units), (12, 4))
        ledger = InventoryLedger.objects.get()
        self.assertEqual((ledger.movement_type, ledger.delta_base_units,
                          ledger.balance_before, ledger.balance_after), ("OUTBOUND", -6, 18, 12))
        self.assertEqual(self.confirm(draft, key).status_code, 200)
        self.assertEqual(self.confirm(draft).status_code, 409)
        self.assertEqual(InventoryLedger.objects.count(), 1)
        listing = self.owner.get("/api/v1/admin/inventory/ledgers?movementType=OUTBOUND&pageSize=1")
        self.assertEqual(listing.status_code, 200, listing.content)
        row = listing.json()["data"]["items"][0]
        self.assertEqual((row["documentNo"], row["baseUnit"], row["note"]),
                         (draft["documentNo"], "瓶", "损坏两箱"))
        detail = self.owner.get(f"/api/v1/admin/inventory/ledgers/{ledger.id}")
        self.assertEqual(detail.json()["data"]["unitVersionId"], str(self.sku.current_unit_id))
        self.assertNotIn("price", json.dumps(row).lower())
        keyword = self.owner.get(f"/api/v1/admin/inventory/ledgers?keyword={draft['documentNo']}")
        self.assertEqual(keyword.json()["data"]["total"], 1)
        by_sku = self.owner.get(f"/api/v1/admin/inventory/balances?skuId={self.sku.id}")
        self.assertEqual(by_sku.json()["data"]["total"], 1)

    def test_ledger_query_keeps_existing_inbound_source(self):
        created = self.post(self.owner, "/api/v1/admin/inventory/inbounds", {
            "warehouseId": str(self.warehouse.id), "reason": "测试入库",
            "items": [{"skuId": str(self.sku.id), "quantity": 1, "unit": "SALE"}],
        }, str(uuid.uuid4()))
        self.assertEqual(created.status_code, 201, created.content)
        inbound_id = created.json()["data"]["inboundId"]
        confirmed = self.post(self.owner, f"/api/v1/admin/inventory/inbounds/{inbound_id}/confirm",
                              {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        listing = self.owner.get("/api/v1/admin/inventory/ledgers?movementType=INBOUND")
        self.assertEqual(listing.status_code, 200, listing.content)
        row = listing.json()["data"]["items"][0]
        self.assertEqual((row["documentId"], row["deltaBaseUnits"], row["baseUnit"]),
                         (inbound_id, 6, "瓶"))

    def test_insufficient_available_and_multiline_failure_roll_back(self):
        balance = self.stock(on_hand=12, reserved=8)
        draft = self.draft().json()["data"]
        failed = self.confirm(draft)
        self.assertEqual(failed.status_code, 409, failed.content)
        self.assertEqual(failed.json()["error"]["code"], "INSUFFICIENT_STOCK")
        balance.refresh_from_db()
        self.assertEqual((balance.on_hand_base_units, balance.reserved_base_units), (12, 8))
        self.assertEqual(OutboundDocument.objects.get().status, "DRAFT")
        self.assertFalse(InventoryLedger.objects.exists())
        balance.reserved_base_units = 0
        balance.save(update_fields=["reserved_base_units"])
        multi = self.draft(items=[
            {"skuId": str(self.sku.id), "quantity": 1, "unit": "BASE"},
            {"skuId": str(self.other.id), "quantity": 1, "unit": "BASE"},
        ]).json()["data"]
        failed = self.confirm(multi)
        self.assertEqual(failed.status_code, 409, failed.content)
        balance.refresh_from_db()
        self.assertEqual(balance.on_hand_base_units, 12)
        self.assertFalse(InventoryLedger.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action_code="inventory.outbound.confirm").exists())

    def test_creation_idempotency_revision_and_unit_change(self):
        self.stock()
        key = str(uuid.uuid4())
        first = self.draft(key=key).json()["data"]
        again = self.draft(key=key).json()["data"]
        self.assertEqual(first["outboundId"], again["outboundId"])
        self.assertEqual(OutboundDocument.objects.count(), 1)
        self.assertEqual(referenced_sku_ids({self.sku.id}), {self.sku.id})
        self.assertEqual(self.draft(key=key, note="different").status_code, 409)
        self.assertEqual(self.confirm(first, revision=2).status_code, 409)
        newer = SkuUnitVersion.objects.create(sku=self.sku, base_unit="瓶", sale_unit="箱", ratio=12)
        self.sku.current_unit = newer
        self.sku.save(update_fields=["current_unit"])
        changed = self.confirm(first)
        self.assertEqual(changed.status_code, 409, changed.content)
        self.assertEqual(changed.json()["error"]["code"], "UNIT_VERSION_CHANGED")
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 12)

    def test_permission_validation_and_filters(self):
        group = PermissionGroup.objects.create(code="stock_reader", name="库存查看")
        GroupPermission.objects.create(group=group, code="inventory.read")
        staff = AdminAccount.objects.create_user("reader", PASSWORD, display_name="查看员", kind="STAFF")
        AccountGroup.objects.create(account=staff, group=group)
        reader = self.login("reader")
        self.assertEqual(reader.get("/api/v1/admin/inventory/outbounds").status_code, 200)
        self.assertEqual(reader.get("/api/v1/admin/inventory/ledgers").status_code, 200)
        self.assertEqual(self.post(reader, "/api/v1/admin/inventory/outbounds", {}, str(uuid.uuid4())).status_code, 403)
        self.stock()
        draft = self.draft().json()["data"]
        self.assertEqual(self.confirm(draft, client=reader).status_code, 403)
        self.assertEqual(self.draft(items=[{"skuId": str(self.sku.id), "quantity": 0, "unit": "BASE"}]).status_code, 400)
        self.assertEqual(self.post(self.owner, "/api/v1/admin/inventory/outbounds", [], str(uuid.uuid4())).status_code, 400)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/ledgers?movementType=bad").status_code, 400)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/balances?skuId=bad").status_code, 400)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/outbounds?status=DRAFT").json()["data"]["total"], 1)
        self.assertEqual(self.owner.get(f"/api/v1/admin/inventory/outbounds/{draft['outboundId']}").status_code, 200)

    def test_ledger_source_and_sign_are_database_enforced_and_immutable(self):
        self.stock()
        draft = self.draft().json()["data"]
        self.assertEqual(self.confirm(draft).status_code, 200)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                InventoryLedger.objects.update(note="changed")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                InventoryLedger.objects.all().delete()
        line = OutboundLine.objects.get()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InventoryLedger.objects.create(
                    warehouse=self.warehouse, sku=self.sku, unit_version=self.sku.current_unit,
                    outbound_line=line, movement_type="INBOUND", operation_unit="瓶",
                    operation_quantity=1, ratio=1, delta_base_units=1, balance_before=0,
                    balance_after=1, reason="bad", actor=self.actor)
