import json
import uuid
from unittest.mock import patch

from django.db import DatabaseError, transaction
from django.test import Client, TestCase

from accounts.models import AccountGroup, AdminAccount, AuditLog, GroupPermission, PermissionGroup
from catalog.models import Category, Product, Sku, SkuUnitVersion
from inventory.models import (InventoryBalance, InventoryLedger, StocktakeAction,
                              StocktakeDocument, StocktakeLine, Warehouse)
from inventory.sku_guards import referenced_sku_ids


PASSWORD = "Safe owner passphrase 2026!"


class StocktakeFlowTests(TestCase):
    def setUp(self):
        self.owner_account = AdminAccount.objects.create_user(
            "owner", PASSWORD, display_name="主账号", kind="OWNER")
        self.owner = self.login("owner")
        root = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=root)
        product = Product.objects.create(product_no="STK-P", name="盘点商品",
                                         category=leaf, fulfillment_kind="SHIP")
        self.first = self.make_sku(product, "STK-A", uuid.UUID(int=101))
        self.second = self.make_sku(product, "STK-B", uuid.UUID(int=102))
        self.warehouse = Warehouse.objects.create(code="MAIN", name="中心仓", is_default=True)

    def make_sku(self, product, code, identifier):
        sku = Sku.objects.create(id=identifier, product=product, sku_code=code,
                                 spec_key=code, list_price_fen=1000)
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="瓶", sale_unit="箱", ratio=6)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        return sku

    def post(self, client, path, payload, key=None):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def login(self, name):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        result = self.post(client, "/api/v1/admin/auth/login",
                           {"loginName": name, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.content)
        return client

    def stock(self, sku, on_hand=10, reserved=0):
        return InventoryBalance.objects.create(warehouse=self.warehouse, sku=sku,
                                               on_hand_base_units=on_hand,
                                               reserved_base_units=reserved)

    def create(self, skus=None, key=None, client=None):
        return self.post(client or self.owner, "/api/v1/admin/inventory/stocktakes", {
            "warehouseId": str(self.warehouse.id),
            "skuIds": [str(sku.id) for sku in (skus or [self.first])],
        }, key or str(uuid.uuid4()))

    def submit(self, document, items, revision=1, key=None, client=None):
        return self.post(client or self.owner,
                         f"/api/v1/admin/inventory/stocktakes/{document['stocktakeId']}/submit",
                         {"expectedRevision": revision, "items": items}, key or str(uuid.uuid4()))

    def item(self, sku, count, reason):
        return {"skuId": str(sku.id), "countedBaseUnits": count, "reason": reason}

    def review(self, document, action, revision=2, key=None, client=None, reason="重新盘点"):
        values = {"expectedRevision": revision}
        if action == "return":
            values["reason"] = reason
        return self.post(client or self.owner,
                         f"/api/v1/admin/inventory/stocktakes/{document['stocktakeId']}/{action}",
                         values, key or str(uuid.uuid4()))

    def test_count_review_gain_loss_and_immutable_ledger(self):
        self.stock(self.first, 10, 2)
        self.stock(self.second, 9, 1)
        created = self.create([self.first, self.second])
        self.assertEqual(created.status_code, 201, created.content)
        document = created.json()["data"]
        self.assertEqual(document["status"], "COUNTING")
        self.assertEqual(document["items"][0]["bookAtStartBaseUnits"], 10)
        self.assertEqual(referenced_sku_ids({self.first.id}), {self.first.id})
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/stocktakes").json()["data"]["total"], 1)
        submitted = self.submit(document, [
            self.item(self.first, 12, "盘盈两瓶"),
            self.item(self.second, 7, "破损两瓶"),
        ])
        self.assertEqual(submitted.status_code, 200, submitted.content)
        self.assertEqual(submitted.json()["data"]["status"], "PENDING_REVIEW")
        self.assertEqual(InventoryBalance.objects.get(sku=self.first).on_hand_base_units, 10)
        self.assertEqual(InventoryLedger.objects.count(), 0)
        approved = self.review(document, "approve")
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["data"]["status"], "APPROVED")
        self.assertEqual(InventoryBalance.objects.get(sku=self.first).on_hand_base_units, 12)
        self.assertEqual(InventoryBalance.objects.get(sku=self.second).on_hand_base_units, 7)
        ledgers = list(InventoryLedger.objects.order_by("sku_id"))
        self.assertEqual([row.delta_base_units for row in ledgers], [2, -2])
        self.assertTrue(all(row.movement_type == "ADJUSTMENT" for row in ledgers))
        self.assertEqual([row.balance_before for row in ledgers], [10, 9])
        listing = self.owner.get("/api/v1/admin/inventory/ledgers?movementType=ADJUSTMENT")
        self.assertEqual(listing.json()["data"]["total"], 2)
        self.assertEqual(listing.json()["data"]["items"][0]["documentNo"], document["documentNo"])
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                InventoryLedger.objects.update(reason="changed")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                InventoryLedger.objects.all().delete()

    def test_idempotency_revision_return_and_resubmit(self):
        self.stock(self.first)
        key = str(uuid.uuid4())
        first = self.create(key=key).json()["data"]
        self.assertEqual(self.create(key=key).json()["data"]["stocktakeId"], first["stocktakeId"])
        self.assertEqual(self.create([self.second], key=key).json()["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.submit(first, [self.item(self.first, 8, "少两瓶")], revision=2).status_code, 409)
        submit_key = str(uuid.uuid4())
        self.assertEqual(self.submit(first, [self.item(self.first, 8, "少两瓶")], key=submit_key).status_code, 200)
        self.assertEqual(self.submit(first, [self.item(self.first, 8, "少两瓶")], key=submit_key).status_code, 200)
        returned = self.review(first, "return")
        self.assertEqual(returned.status_code, 200, returned.content)
        self.assertEqual(returned.json()["data"]["status"], "COUNTING")
        self.assertIsNone(returned.json()["data"]["items"][0]["countedBaseUnits"])
        return_log = AuditLog.objects.get(action_code="inventory.stocktake.return")
        self.assertEqual(return_log.before["revision"], 2)
        self.assertEqual(return_log.before["items"][0]["countedBaseUnits"], 8)
        self.assertEqual(return_log.before["items"][0]["deltaBaseUnits"], -2)
        replay = self.submit(first, [self.item(self.first, 8, "少两瓶")], key=submit_key)
        self.assertEqual(replay.json()["error"]["code"], "ACTION_ALREADY_APPLIED")
        self.assertEqual(self.submit(first, [self.item(self.first, 9, "少一瓶")], revision=3).status_code, 200)
        self.assertEqual(self.review(first, "approve", revision=4).status_code, 200)
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 9)
        self.assertEqual(InventoryLedger.objects.count(), 1)
        self.assertEqual(StocktakeAction.objects.count(), 4)

    def test_book_change_and_reserved_conflict_preserve_old_balance(self):
        balance = self.stock(self.first, 10, 4)
        document = self.create().json()["data"]
        self.assertEqual(self.submit(document, [self.item(self.first, 3, "短少七瓶")]).status_code, 200)
        reserved = self.review(document, "approve")
        self.assertEqual(reserved.status_code, 409, reserved.content)
        self.assertEqual(reserved.json()["error"]["code"], "RESERVED_STOCK_CONFLICT")
        balance.on_hand_base_units = 11
        balance.save(update_fields=["on_hand_base_units", "updated_at"])
        changed = self.review(document, "approve")
        self.assertEqual(changed.status_code, 409, changed.content)
        self.assertEqual(changed.json()["error"]["code"], "BOOK_CHANGED")
        self.assertEqual(InventoryLedger.objects.count(), 0)
        self.assertEqual(StocktakeDocument.objects.get().status, "PENDING_REVIEW")
        self.assertEqual(self.review(document, "return").status_code, 200)
        self.assertEqual(self.submit(document, [self.item(self.first, 5, "短少六瓶")], revision=3).status_code, 200)
        self.assertEqual(self.review(document, "approve", revision=4).status_code, 200)
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 5)

    def test_multiline_approval_failure_rolls_back_adjustment_and_audit(self):
        self.stock(self.first, 10)
        self.stock(self.second, 10, 5)
        document = self.create([self.first, self.second]).json()["data"]
        submitted = self.submit(document, [self.item(self.first, 11, "多一瓶"),
                                           self.item(self.second, 4, "少六瓶")])
        self.assertEqual(submitted.status_code, 200, submitted.content)
        failed = self.review(document, "approve")
        self.assertEqual(failed.status_code, 409, failed.content)
        self.assertEqual(failed.json()["error"]["code"], "RESERVED_STOCK_CONFLICT")
        self.assertEqual(InventoryBalance.objects.get(sku=self.first).on_hand_base_units, 10)
        self.assertFalse(InventoryLedger.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action_code="inventory.stocktake.approve").exists())
        self.assertEqual(StocktakeDocument.objects.get().status, "PENDING_REVIEW")
        self.assertFalse(StocktakeAction.objects.filter(action="APPROVE").exists())

    def test_inbound_and_outbound_continue_during_count_and_force_recheck(self):
        self.stock(self.first, 10)
        document = self.create().json()["data"]
        inbound = self.post(self.owner, "/api/v1/admin/inventory/inbounds", {
            "warehouseId": str(self.warehouse.id), "reason": "正常补货",
            "items": [{"skuId": str(self.first.id), "quantity": 2, "unit": "BASE"}],
        }, str(uuid.uuid4()))
        self.assertEqual(inbound.status_code, 201, inbound.content)
        inbound_id = inbound.json()["data"]["inboundId"]
        confirmed = self.post(self.owner, f"/api/v1/admin/inventory/inbounds/{inbound_id}/confirm",
                              {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        submitted = self.submit(document, [self.item(self.first, 11, "短少一瓶")])
        self.assertEqual(submitted.status_code, 200, submitted.content)
        self.assertEqual(submitted.json()["data"]["items"][0]["bookAtSubmitBaseUnits"], 12)
        outbound = self.post(self.owner, "/api/v1/admin/inventory/outbounds", {
            "warehouseId": str(self.warehouse.id), "reason": "SAMPLE", "note": "样品领用",
            "items": [{"skuId": str(self.first.id), "quantity": 1, "unit": "BASE"}],
        }, str(uuid.uuid4()))
        self.assertEqual(outbound.status_code, 201, outbound.content)
        outbound_id = outbound.json()["data"]["outboundId"]
        confirmed = self.post(self.owner, f"/api/v1/admin/inventory/outbounds/{outbound_id}/confirm",
                              {"expectedRevision": 1}, str(uuid.uuid4()))
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        detail = self.owner.get(f"/api/v1/admin/inventory/stocktakes/{document['stocktakeId']}")
        self.assertTrue(detail.json()["data"]["items"][0]["bookChanged"])
        failed = self.review(document, "approve")
        self.assertEqual(failed.json()["error"]["code"], "BOOK_CHANGED")
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 11)
        self.assertFalse(InventoryLedger.objects.filter(movement_type="ADJUSTMENT").exists())

    def test_permissions_validation_and_empty_balance(self):
        group = PermissionGroup.objects.create(code="stock_operator", name="库存操作")
        GroupPermission.objects.create(group=group, code="inventory.read")
        GroupPermission.objects.create(group=group, code="inventory.manage")
        staff = AdminAccount.objects.create_user("operator", PASSWORD, display_name="操作员", kind="STAFF")
        AccountGroup.objects.create(account=staff, group=group)
        operator = self.login("operator")
        self.assertEqual(self.create(client=operator).status_code, 201)
        document = self.create([self.second]).json()["data"]
        self.assertEqual(self.submit(document, [self.item(self.second, 2, "实盘两瓶")], client=operator).status_code, 200)
        self.assertEqual(self.review(document, "approve", client=operator).status_code, 403)
        self.assertEqual(self.review(document, "approve").status_code, 200)
        self.assertEqual(InventoryBalance.objects.get(sku=self.second).on_hand_base_units, 2)
        self.assertEqual(self.create([self.first, self.first]).status_code, 400)
        self.assertEqual(self.owner.get("/api/v1/admin/inventory/stocktakes?status=BAD").status_code, 400)
        self.assertEqual(self.owner.get(f"/api/v1/admin/inventory/stocktakes/{document['stocktakeId']}").status_code, 200)
        self.assertEqual(self.create().status_code, 201)
        fresh = StocktakeDocument.objects.order_by("-created_at").first()
        self.assertEqual(self.submit({"stocktakeId": str(fresh.id)}, [self.item(self.first, 1, "")]).status_code, 400)
        self.assertEqual(StocktakeDocument.objects.get(id=fresh.id).status, "COUNTING")

    def test_approval_database_error_rolls_back_all_writes(self):
        self.stock(self.first, 10)
        document = self.create().json()["data"]
        self.assertEqual(self.submit(document, [self.item(self.first, 11, "多一瓶")]).status_code, 200)
        with patch("inventory.stocktake_service.InventoryLedger.objects.create", side_effect=DatabaseError("failed")):
            with self.assertRaises(DatabaseError):
                self.review(document, "approve")
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 10)
        self.assertEqual(StocktakeDocument.objects.get().status, "PENDING_REVIEW")
        self.assertFalse(InventoryLedger.objects.exists())
