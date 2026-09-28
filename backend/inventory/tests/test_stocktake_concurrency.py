"""Independent PostgreSQL connections cannot approve stale counts on one SKU."""

import threading
import uuid
from types import SimpleNamespace

from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import AdminAccount
from catalog.models import Category, Product, Sku, SkuUnitVersion
from inventory.models import InventoryBalance, InventoryLedger, Warehouse
from inventory.stocktake_service import create_stocktake, review_stocktake, submit_stocktake
from inventory.validation import InventoryError


class StocktakeConcurrencyTests(TransactionTestCase):
    def test_concurrent_approvals_post_only_one_adjustment(self):
        actor = AdminAccount.objects.create_user(
            "stock-owner", "Safe owner passphrase 2026!", display_name="主账号", kind="OWNER")
        category = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=category)
        product = Product.objects.create(product_no="STK-CONCUR", name="商品", category=leaf,
                                         fulfillment_kind="SHIP")
        sku = Sku.objects.create(product=product, sku_code="STK-CONCUR-SKU", spec_key="single",
                                 list_price_fen=1000)
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit="瓶", sale_unit="箱", ratio=6)
        sku.current_unit = unit
        sku.save(update_fields=["current_unit"])
        warehouse = Warehouse.objects.create(code="MAIN", name="中心仓", is_default=True)
        InventoryBalance.objects.create(warehouse=warehouse, sku=sku, on_hand_base_units=10,
                                        reserved_base_units=0)
        request = SimpleNamespace(request_id=uuid.uuid4())
        documents = []
        for count in (11, 12):
            document = create_stocktake(request, actor, {
                "warehouseId": str(warehouse.id), "skuIds": [str(sku.id)],
            }, uuid.uuid4())
            submitted = submit_stocktake(request, actor, uuid.UUID(document["stocktakeId"]), {
                "expectedRevision": 1,
                "items": [{"skuId": str(sku.id), "countedBaseUnits": count, "reason": "盘点差异"}],
            }, uuid.uuid4())
            documents.append(submitted)
        gate = threading.Barrier(2)
        results, failures = [], []

        def approve(document):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                result = review_stocktake(
                    SimpleNamespace(request_id=uuid.uuid4()), actor,
                    uuid.UUID(document["stocktakeId"]), {"expectedRevision": 2},
                    uuid.uuid4(), "APPROVE")
                results.append(result["status"])
            except InventoryError as exc:
                failures.append(exc.code)
            finally:
                connection.close()

        threads = [threading.Thread(target=approve, args=(document,)) for document in documents]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results, ["APPROVED"])
        self.assertEqual(failures, ["BOOK_CHANGED"])
        self.assertEqual(InventoryLedger.objects.count(), 1)
        self.assertIn(InventoryBalance.objects.get().on_hand_base_units, (11, 12))
