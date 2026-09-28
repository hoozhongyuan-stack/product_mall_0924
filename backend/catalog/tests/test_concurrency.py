"""Exercise the product/category lock order against PostgreSQL connections."""

import json
import threading
import time
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase

from accounts.models import AdminAccount
from catalog.models import Category, Product, Sku
from catalog.views import active_leaf


class BatchCategoryConcurrencyTests(TransactionTestCase):
    def api_post(self, client, path, payload, key=None):
        headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
        if key:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)

    def owner_client(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/admin/auth/csrf")
        logged_in = self.api_post(client, "/api/v1/admin/auth/login", {
            "loginName": "owner", "password": "Safe owner passphrase 2026!",
        })
        self.assertEqual(logged_in.status_code, 200, logged_in.content)
        return client

    def test_batch_and_single_category_edit_do_not_deadlock(self):
        AdminAccount.objects.create_user("owner", "Safe owner passphrase 2026!",
                                         display_name="主账号", kind=AdminAccount.Kind.OWNER)
        root = Category.objects.create(name="商品")
        source = Category.objects.create(name="原分类", parent=root)
        target = Category.objects.create(name="目标分类", parent=root)
        products = [Product.objects.create(product_no=f"P-{number}", name=f"商品 {number}",
                                           category=source, fulfillment_kind=Product.Fulfillment.SHIP)
                    for number in (1, 2)]
        skus = [Sku.objects.create(product=product, sku_code=f"S-{number}",
                                   spec_key="single", list_price_fen=100)
                for number, product in enumerate(products, 1)]
        batch_client = self.owner_client()
        single_client = self.owner_client()
        preview = self.api_post(batch_client, "/api/v1/admin/products/batch-category/preview", {
            "skuIds": [str(sku.id) for sku in skus], "categoryId": str(target.id),
        })
        self.assertEqual(preview.status_code, 200, preview.content)
        payload = {"categoryId": str(target.id), "previewToken": preview.json()["data"]["previewToken"],
                   "items": [{"productId": str(product.id), "expectedRevision": 1} for product in products]}

        single_has_product_lock = threading.Event()
        release_single = threading.Event()
        batch_started = threading.Event()
        results = {}
        errors = {}
        backend_pid = []

        def gated_leaf(category_id):
            if threading.current_thread().name == "single-edit":
                single_has_product_lock.set()
                if not release_single.wait(10):
                    raise TimeoutError("single edit was not released")
            return active_leaf(category_id)

        def single_edit():
            close_old_connections()
            try:
                results["single"] = single_client.patch(
                    f"/api/v1/admin/products/{products[1].id}",
                    data=json.dumps({"categoryId": str(target.id), "expectedRevision": 1}),
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=single_client.cookies["csrftoken"].value)
            except Exception as exc:  # surfaced in the test thread below
                errors["single"] = exc
            finally:
                connection.close()

        def batch_edit():
            close_old_connections()
            try:
                connection.ensure_connection()
                backend_pid.append(connection.connection.info.backend_pid)
                batch_started.set()
                results["batch"] = self.api_post(batch_client,
                    "/api/v1/admin/products/batch-category", payload,
                    "75c6e294-ddda-4ae8-80d9-7caf6cf50a14")
            except Exception as exc:
                errors["batch"] = exc
            finally:
                connection.close()

        with patch("catalog.views.active_leaf", side_effect=gated_leaf):
            single = threading.Thread(target=single_edit, name="single-edit")
            batch = threading.Thread(target=batch_edit, name="batch-edit")
            single.start()
            try:
                self.assertTrue(single_has_product_lock.wait(5))
                batch.start()
                self.assertTrue(batch_started.wait(5))
                for _ in range(150):
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                                       [backend_pid[0]])
                        row = cursor.fetchone()
                    if row and row[0] == "Lock":
                        break
                    time.sleep(0.02)
                else:
                    self.fail("batch request did not wait for the locked product")
            finally:
                release_single.set()
                single.join(10)
                if batch_started.is_set():
                    batch.join(10)
        self.assertFalse(single.is_alive())
        self.assertFalse(batch.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(results["single"].status_code, 200, results["single"].content)
        self.assertEqual(results["batch"].status_code, 200, results["batch"].content)
        self.assertEqual(results["batch"].json()["data"]["failedCount"], 1)
