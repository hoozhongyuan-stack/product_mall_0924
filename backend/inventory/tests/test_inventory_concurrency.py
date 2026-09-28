"""Two PostgreSQL connections confirming receipts for the same new balance."""

import threading
import uuid
from types import SimpleNamespace

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import AdminAccount
from catalog.models import Category, Product, Sku, SkuUnitVersion
from inventory.models import InventoryBalance, InventoryLedger, Warehouse
from inventory.service import confirm_inbound, create_inbound


class InventoryConcurrencyTests(TransactionTestCase):
    def test_concurrent_first_inbounds_accumulate_without_lost_update(self):
        actor = AdminAccount.objects.create_user(
            "inventory-owner", "Safe owner passphrase 2026!", display_name="主账号", kind="OWNER")
        category = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=category)
        product = Product.objects.create(product_no="I-P", name="商品", category=leaf,
                                         fulfillment_kind="SHIP")
        sku = Sku.objects.create(product=product, sku_code="I-SKU", spec_key="single",
                                 list_price_fen=1000)
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="瓶", sale_unit="箱", ratio=6)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        warehouse = Warehouse.objects.create(code="MAIN", name="中心仓", is_default=True)
        request = SimpleNamespace(request_id=uuid.uuid4())
        documents = [create_inbound(request, actor, {
            "warehouseId": str(warehouse.id), "reason": "测试入库",
            "items": [{"skuId": str(sku.id), "quantity": 2, "unit": "SALE"}],
        }, uuid.uuid4()) for _ in range(2)]
        gate = threading.Barrier(2)
        results, failures = [], []

        def confirm(document):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                result = confirm_inbound(SimpleNamespace(request_id=uuid.uuid4()), actor,
                                         uuid.UUID(document["inboundId"]), 1, uuid.uuid4())
                results.append(result["status"])
            except Exception as exc:
                failures.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=confirm, args=(document,)) for document in documents]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertFalse(failures, failures)
        self.assertEqual(results, ["CONFIRMED", "CONFIRMED"])
        self.assertEqual(InventoryBalance.objects.get().on_hand_base_units, 24)
        self.assertEqual(InventoryLedger.objects.count(), 2)
