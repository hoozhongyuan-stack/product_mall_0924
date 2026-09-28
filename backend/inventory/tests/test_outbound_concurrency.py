"""Two PostgreSQL connections race to deplete the same available balance."""

import threading
import uuid
from types import SimpleNamespace

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import AdminAccount
from catalog.models import Category, Product, Sku, SkuUnitVersion
from inventory.models import InventoryBalance, InventoryLedger, Warehouse
from inventory.outbound_service import confirm_outbound, create_outbound
from inventory.validation import InventoryError


class OutboundConcurrencyTests(TransactionTestCase):
    def test_two_outbounds_cannot_consume_same_available_units(self):
        actor = AdminAccount.objects.create_user(
            "inventory-owner", "Safe owner passphrase 2026!", display_name="主账号", kind="OWNER")
        category = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=category)
        product = Product.objects.create(product_no="O-P", name="商品", category=leaf,
                                         fulfillment_kind="SHIP")
        sku = Sku.objects.create(product=product, sku_code="O-SKU", spec_key="single",
                                 list_price_fen=1000)
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="瓶", sale_unit="箱", ratio=6)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        warehouse = Warehouse.objects.create(code="MAIN", name="中心仓", is_default=True)
        InventoryBalance.objects.create(warehouse=warehouse, sku=sku, on_hand_base_units=12,
                                        reserved_base_units=2)
        request = SimpleNamespace(request_id=uuid.uuid4())
        documents = [create_outbound(request, actor, {
            "warehouseId": str(warehouse.id), "reason": "SAMPLE", "note": "并发测试",
            "items": [{"skuId": str(sku.id), "quantity": 8, "unit": "BASE"}],
        }, uuid.uuid4()) for _ in range(2)]
        gate = threading.Barrier(2)
        results, failures = [], []

        def confirm(document):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                result = confirm_outbound(SimpleNamespace(request_id=uuid.uuid4()), actor,
                                          uuid.UUID(document["outboundId"]), 1, uuid.uuid4())
                results.append(result["status"])
            except InventoryError as exc:
                failures.append(exc.code)
            finally:
                connection.close()

        threads = [threading.Thread(target=confirm, args=(document,)) for document in documents]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results, ["CONFIRMED"])
        self.assertEqual(failures, ["INSUFFICIENT_STOCK"])
        balance = InventoryBalance.objects.get()
        self.assertEqual((balance.on_hand_base_units, balance.reserved_base_units), (4, 2))
        self.assertEqual(InventoryLedger.objects.count(), 1)
