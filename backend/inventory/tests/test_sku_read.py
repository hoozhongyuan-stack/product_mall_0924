"""Inventory selection reads, independent of catalog management permissions."""
import uuid
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from accounts.models import AccountGroup, AdminAccount, GroupPermission, PermissionGroup
from catalog.models import Asset, Sku, SkuUnitVersion, SpecAxis, SpecOption, SkuSpecSelection
from inventory.models import InventoryBalance, Warehouse
from inventory.tests import test_inventory_flow as fixtures
PASSWORD = fixtures.PASSWORD


class InventorySkuReadTests(TestCase):
    setUp = fixtures.InventoryFlowTests.setUp
    login = fixtures.InventoryFlowTests.login
    post = fixtures.InventoryFlowTests.post

    def read(self, **query):
        return self.owner.get('/api/v1/admin/inventory/skus', query)

    def add_sku(self, code):
        sku = Sku.objects.create(product=self.sku.product, sku_code=code, spec_key=code, list_price_fen=500)
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit='瓶', sale_unit='箱', ratio=6)
        Sku.objects.filter(pk=sku.pk).update(current_unit=unit)
        return sku

    def test_ordered_specs_and_optional_balance_without_price_disclosure(self):
        for order, name, value in [(2, '容量', '500mL'), (1, '颜色', '红色')]:
            axis = SpecAxis.objects.create(product=self.sku.product, name=name, sort_order=order)
            option = SpecOption.objects.create(axis=axis, value=value, sort_order=1)
            SkuSpecSelection.objects.create(sku=self.sku, axis=axis, option=option)
        warehouse = Warehouse.objects.create(code='W1', name='中心仓')
        InventoryBalance.objects.create(warehouse=warehouse, sku=self.sku, on_hand_base_units=20, reserved_base_units=3)
        row = self.read(warehouseId=warehouse.pk).json()['data']['items'][0]
        self.assertEqual(row['specs'], [{'name': '颜色', 'value': '红色'}, {'name': '容量', 'value': '500mL'}])
        self.assertEqual(row['productNo'], 'P-1')
        self.assertEqual(row['warehouseStock'], {'warehouseId': str(warehouse.pk), 'onHandBaseUnits': 20, 'reservedBaseUnits': 3, 'availableBaseUnits': 17})
        self.assertIsNone(row['mainImage'])
        self.assertNotIn('listPriceFen', row)
        self.assertIsNone(self.read().json()['data']['items'][0]['warehouseStock'])
        self.assertEqual(self.read(keyword='红色').json()['data']['total'], 1)
        self.assertEqual(self.read(keyword='P-1').json()['data']['total'], 1)

    def test_missing_balance_is_zero_and_invalid_warehouse_is_rejected(self):
        warehouse = Warehouse.objects.create(code='W1', name='空仓')
        row = self.read(warehouseId=warehouse.pk).json()['data']['items'][0]
        self.assertEqual(row['warehouseStock']['availableBaseUnits'], 0)
        self.assertEqual(self.read(warehouseId='bad').status_code, 400)
        self.assertEqual(self.read(warehouseId=uuid.uuid4()).status_code, 404)

    def test_page_queries_are_bounded(self):
        warehouse = Warehouse.objects.create(code='W1', name='中心仓')
        for index in range(8):
            sku = self.add_sku(f'EXTRA-{index}')
            InventoryBalance.objects.create(warehouse=warehouse, sku=sku, on_hand_base_units=index)
        with CaptureQueriesContext(connection) as first:
            one = self.read(warehouseId=warehouse.pk, pageSize=1)
        with CaptureQueriesContext(connection) as many:
            all_rows = self.read(warehouseId=warehouse.pk, pageSize=10)
        self.assertEqual(one.status_code, 200)
        self.assertEqual(all_rows.json()['data']['total'], 9)
        self.assertLessEqual(len(many), len(first) + 1)
        self.assertIn('warehouseStock', all_rows.json()['data']['items'][0])

    def test_inventory_permission_reads_selection_without_catalog_permission(self):
        user = AdminAccount.objects.create_user('stock', PASSWORD, kind='STAFF', display_name='库管')
        group = PermissionGroup.objects.create(code='stock', name='库管')
        GroupPermission.objects.create(group=group, code='inventory.read')
        AccountGroup.objects.create(account=user, group=group)
        client = self.login('stock')
        response = client.get('/api/v1/admin/inventory/skus')
        self.assertEqual(response.status_code, 200)
        self.assertIn('specs', response.json()['data']['items'][0])

    def test_thumbnail_helper_requires_current_stock_product_main_image(self):
        from inventory.catalog_media import asset_is_available_to_inventory_reader
        image = Asset.objects.create(kind='IMAGE', content_type='image/png', byte_size=4, width=1,
                                     height=1, sha256='0' * 64, stored_name='product/test.png', original_name='test.png', created_by=AdminAccount.objects.get(login_name='owner'))
        self.assertFalse(asset_is_available_to_inventory_reader(image.id))
        product = self.sku.product
        product.main_image = image
        product.save(update_fields=['main_image'])
        self.assertTrue(asset_is_available_to_inventory_reader(image.id))
        Sku.objects.filter(pk=self.sku.pk).update(current_unit=None)
        self.assertFalse(asset_is_available_to_inventory_reader(image.id))

    def test_inventory_reader_has_only_bound_main_image_file_access(self):
        from unittest.mock import patch
        from django.http import HttpResponse
        user = AdminAccount.objects.create_user('stock', PASSWORD, kind='STAFF', display_name='库管')
        group = PermissionGroup.objects.create(code='stock', name='库管')
        GroupPermission.objects.create(group=group, code='inventory.read')
        AccountGroup.objects.create(account=user, group=group)
        client = self.login('stock')
        image = Asset.objects.create(kind='IMAGE', content_type='image/png', byte_size=4, width=1,
                                     height=1, sha256='0' * 64, stored_name='product/test.png',
                                     original_name='test.png', created_by=AdminAccount.objects.get(login_name='owner'))
        path = f'/api/v1/admin/assets/{image.id}/file'
        with patch('catalog.media_views.file_response', return_value=HttpResponse(b'image')):
            self.assertEqual(client.get(path).status_code, 403)
            product = self.sku.product
            product.main_image = image
            product.save(update_fields=['main_image'])
            self.assertEqual(client.get(path).status_code, 200)
            summary = client.get('/api/v1/admin/inventory/skus').json()['data']['items'][0]
            self.assertEqual(summary['mainImage'], {'assetId': str(image.id), 'adminUrl': path})
            self.assertEqual(client.get('/api/v1/admin/assets').status_code, 403)
            product.main_image = None
            product.save(update_fields=['main_image'])
            self.assertEqual(client.get(path).status_code, 403)
