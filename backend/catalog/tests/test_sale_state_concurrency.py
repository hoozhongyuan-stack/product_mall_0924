"""Concurrent SKU changes for one product must preserve the aggregate."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from django.db import close_old_connections
from django.test import TransactionTestCase

from accounts.models import AdminAccount
from catalog.models import Asset, Category, Product, Sku, SkuUnitVersion
from catalog.sale_state import change_sku_sale_status


class SaleStateConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = AdminAccount.objects.create_user(
            "sale-owner", "Long test password 2026!", display_name="主账号",
            kind=AdminAccount.Kind.OWNER)
        root = Category.objects.create(name="酒类")
        leaf = Category.objects.create(name="白酒", parent=root)
        image = Asset.objects.create(
            kind=Asset.Kind.IMAGE, content_type="image/png", byte_size=1,
            width=1, height=1, sha256="0" * 64, original_name="sale.png",
            stored_name="product/sale-concurrency.png", created_by=self.owner)
        self.product = Product.objects.create(product_no="P-CONCURRENT", name="并发商品",
                                              category=leaf, fulfillment_kind="SHIP", main_image=image)
        self.skus = []
        for index in range(2):
            sku = Sku.objects.create(product=self.product, sku_code=f"S-CONCURRENT-{index}",
                                     spec_key=str(index), list_price_fen=1000)
            unit = SkuUnitVersion.objects.create(sku=sku, base_unit="件", sale_unit="件", ratio=1)
            sku.current_unit = unit
            sku.save(update_fields=["current_unit"])
            self.skus.append(sku)

    def change_together(self, status, revision):
        barrier = Barrier(3)

        def change(sku_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                actor = AdminAccount.objects.get(id=self.owner.id)
                request = SimpleNamespace(request_id=uuid.uuid4())
                return change_sku_sale_status(request, actor, sku_id, revision, status,
                                              "sku.status.concurrent-test")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(change, sku.id) for sku in self.skus]
            barrier.wait(timeout=10)
            return [future.result(timeout=10) for future in futures]

    def test_two_simultaneous_first_and_last_sku_changes(self):
        self.change_together(Sku.SaleStatus.ON_SALE, 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.ON_SALE)
        self.assertTrue(self.product.ever_on_sale)
        self.assertEqual(self.product.revision, 2)
        self.assertEqual(Sku.objects.filter(product=self.product, sale_status="ON_SALE").count(), 2)

        self.change_together(Sku.SaleStatus.OFF_SALE, 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, Product.Status.OFF_SALE)
        self.assertEqual(self.product.revision, 3)
        self.assertEqual(Sku.objects.filter(product=self.product, sale_status="ON_SALE").count(), 0)
