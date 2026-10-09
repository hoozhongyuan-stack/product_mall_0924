"""Unit is an explicit specification axis; stock identity follows other axes."""
from django.test import TestCase

from catalog.models import Product, Sku
from inventory.models import StockPoolSku
from . import test_spec_edit as helpers


class UnitConversionTests(TestCase):
    setUp = helpers.SpecEditTests.setUp
    send = helpers.SpecEditTests.send
    post = helpers.SpecEditTests.post
    login = helpers.SpecEditTests.login
    sku = staticmethod(helpers.SpecEditTests.sku)
    detail = helpers.SpecEditTests.detail
    preview = helpers.SpecEditTests.preview
    stock_sku = helpers.SpecEditTests.stock_sku

    def converted_payload(self):
        product = Product.objects.get(pk=self.product['productId'])
        return {'productNo': 'P-UNIT', 'name': '换算商品', 'categoryId': str(product.category_id),
                'fulfillmentKind': 'SHIP', 'specAxes': [
                    {'clientKey': 'degree', 'name': '度数', 'sortOrder': 0, 'options': [
                        {'clientKey': '53', 'value': '53°', 'sortOrder': 0},
                        {'clientKey': '43', 'value': '43°', 'sortOrder': 1}]},
                    {'clientKey': 'unit', 'name': '单位', 'sortOrder': 1, 'options': [
                        {'clientKey': 'bottle', 'value': '瓶', 'sortOrder': 0},
                        {'clientKey': 'case', 'value': '箱', 'sortOrder': 1}]}],
                'unitConversion': {'axisKey': 'unit', 'baseOptionKey': 'bottle', 'ratios': [
                    {'optionKey': 'bottle', 'ratio': 1}, {'optionKey': 'case', 'ratio': 6}]},
                'skus': [{key: value for key, value in self.sku(f'{degree}-{unit}', [degree, unit]).items()
                          if key != 'unit'} for degree in ['53', '43'] for unit in ['bottle', 'case']]}

    def create_converted(self):
        result = self.post(self.owner, '/api/v1/admin/products', self.converted_payload())
        self.assertEqual(result.status_code, 201, result.content)
        self.product = result.json()['data']
        self.path = f"/api/v1/admin/products/{self.product['productId']}/specs"
        return self.detail()

    def edit(self):
        detail = self.detail()
        axes = [{**axis, 'clientKey': axis['id'], 'options': [
            {**option, 'clientKey': option['id']} for option in axis['options']]} for axis in detail['specAxes']]
        skus = [{'id': row['skuId'], 'expectedSkuRevision': row['skuRevision'], 'skuCode': row['skuCode'],
                 'specOptionKeys': row['specOptionIds'], 'listPriceFen': row['listPriceFen'],
                 'saleStatus': row['saleStatus'], 'gradePrices': row['gradePrices']} for row in detail['skus']]
        return {'expectedRevision': detail['productRevision'], 'specAxes': axes,
                'unitConversion': detail['unitConversion'], 'skus': skus}

    def save(self, payload):
        payload = {**payload, 'previewToken': self.preview(payload)['previewToken']}
        return self.send(self.owner, 'put', self.path, payload)

    def test_create_derives_units_and_separates_nonunit_combinations(self):
        detail = self.create_converted()
        self.assertIsNotNone(detail['unitConversion'])
        rows = {row['skuCode']: row for row in detail['skus']}
        self.assertEqual(rows['53-case']['unit']['ratio'], 6)
        bindings = {row.sku.sku_code: row.pool_id for row in StockPoolSku.objects.select_related('sku')
                    .filter(sku__product_id=detail['productId'])}
        self.assertEqual(bindings['53-case'], bindings['53-bottle'])
        self.assertNotEqual(bindings['43-case'], bindings['53-case'])

    def test_invalid_conversion_and_forged_units_roll_back(self):
        for mutate in [lambda p: p['unitConversion']['ratios'].pop(),
                       lambda p: p['unitConversion'].update(baseOptionKey=[]),
                       lambda p: p['unitConversion'].update(axisKey={}),
                       lambda p: p['specAxes'][1].update(clientKey=' unit '),
                       lambda p: p['unitConversion']['ratios'][0].update(ratio=2),
                       lambda p: p['skus'][1].update(unit={'baseUnit': '瓶', 'saleUnit': '箱', 'ratio': 7})]:
            payload = self.converted_payload()
            mutate(payload)
            result = self.post(self.owner, '/api/v1/admin/products', payload)
            self.assertEqual(result.status_code, 400, result.content)
            self.assertFalse(Product.objects.filter(product_no='P-UNIT').exists())

    def test_price_edit_retains_ids_and_history_free_anchor_removal(self):
        self.create_converted()
        payload = self.edit()
        original_ids = {row['id'] for row in payload['skus']}
        payload['skus'][0]['listPriceFen'] += 1
        result = self.save(payload)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual({row['skuId'] for row in result.json()['data']['skus']}, original_ids)
        payload = self.edit()
        payload['skus'] = [row for row in payload['skus'] if row['skuCode'].startswith('43')]
        payload['specAxes'][0]['options'] = payload['specAxes'][0]['options'][1:]
        result = self.save(payload)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(StockPoolSku.objects.filter(sku__product_id=self.product['productId']).count(), 2)

    def test_history_on_member_blocks_group_topology_change(self):
        detail = self.create_converted()
        case = next(row for row in detail['skus'] if row['skuCode'] == '53-case')
        self.stock_sku(case['skuId'])
        payload = self.edit()
        next(row for row in payload['unitConversion']['ratios'] if row['ratio'] != 1)['ratio'] = 8
        response = self.post(self.owner, self.path + '/preview', payload)
        self.assertEqual(response.status_code, 409, response.content)

    def test_configured_units_cannot_be_changed_through_standalone_api(self):
        detail = self.create_converted()
        sku = detail['skus'][0]
        result = self.send(self.owner, 'put', f"/api/v1/admin/skus/{sku['skuId']}/unit", {
            'expectedRevision': sku['skuRevision'], 'unit': {'baseUnit': '瓶', 'saleUnit': '箱', 'ratio': 7}})
        self.assertEqual(result.status_code, 409, result.content)

    def test_history_free_configuration_can_be_disabled(self):
        self.create_converted()
        payload = self.edit()
        detail_by_id = {row['skuId']: row for row in self.detail()['skus']}
        payload['unitConversion'] = None
        for row in payload['skus']:
            old = detail_by_id[row['id']]['unit']
            row['unit'] = {'baseUnit': old['saleUnit'], 'saleUnit': old['saleUnit'], 'ratio': 1}
        result = self.save(payload)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIsNone(result.json()['data']['unitConversion'])
        self.assertEqual(StockPoolSku.objects.filter(sku__product_id=self.product['productId'])
                         .values('pool_id').distinct().count(), 4)

    def test_automatic_group_drives_public_availability_and_mixed_cart_guard(self):
        from types import SimpleNamespace
        from catalog.presentation import public_product_data
        from inventory.models import Warehouse, InventoryBalance
        from inventory.reservations import lock_default_balances, reserve_order_lines, ReservationError
        from inventory.pool_access import resolve_anchor_id
        detail = self.create_converted()
        skus = list(Sku.objects.filter(product_id=detail['productId']))
        warehouse = Warehouse.objects.create(code='UNIT-STOCK', name='单位仓', is_default=True)
        bottle = next(sku for sku in skus if sku.sku_code == '53-bottle')
        case = next(sku for sku in skus if sku.sku_code == '53-case')
        balance = InventoryBalance.objects.create(warehouse=warehouse, sku_id=resolve_anchor_id(bottle.id),
                                                 on_hand_base_units=10)
        Sku.objects.filter(product_id=detail['productId']).update(sale_status='ON_SALE')
        Product.objects.filter(pk=detail['productId']).update(status='ON_SALE', ever_on_sale=True)
        public = public_product_data(Product.objects.get(pk=detail['productId']))
        visible = {row['skuCode']: row for row in public['skus']}
        self.assertEqual(visible['53-bottle']['availableQuantity'], 10)
        self.assertEqual(visible['53-case']['availableQuantity'], 1)
        self.assertEqual(visible['43-bottle']['availableQuantity'], 0)
        locked = lock_default_balances(warehouse.id, [bottle.id, case.id])
        with self.assertRaises(ReservationError):
            reserve_order_lines([SimpleNamespace(sku_id=bottle.id, warehouse_id=warehouse.id, base_quantity=5),
                                 SimpleNamespace(sku_id=case.id, warehouse_id=warehouse.id, base_quantity=6)], locked)
        balance.refresh_from_db()
        self.assertEqual(balance.reserved_base_units, 0)

    def test_missing_base_sku_and_manual_binding_rejected(self):
        from inventory.pool_access import bind_sku_to_pool, PoolBindingError
        payload = self.converted_payload()
        payload['skus'] = [row for row in payload['skus'] if not row['skuCode'].endswith('bottle')]
        result = self.post(self.owner, '/api/v1/admin/products', payload)
        self.assertEqual(result.status_code, 400, result.content)
        detail = self.create_converted()
        member = StockPoolSku.objects.select_related('sku').filter(sku__product_id=detail['productId']).first()
        with self.assertRaises(PoolBindingError):
            bind_sku_to_pool(member.sku_id, member.pool_id, member.sku.revision)

    def test_history_rejects_disabling_or_omitting_config_and_allows_price_edit(self):
        detail = self.create_converted()
        self.stock_sku(detail['skus'][0]['skuId'])
        payload = self.edit()
        payload['skus'][0]['listPriceFen'] += 1
        result = self.save(payload)
        self.assertEqual(result.status_code, 200, result.content)
        payload = self.edit()
        del payload['unitConversion']
        result = self.post(self.owner, self.path + '/preview', payload)
        self.assertEqual(result.status_code, 400, result.content)
        payload = self.edit()
        by_id = {row['skuId']: row['unit'] for row in self.detail()['skus']}
        for row in payload['skus']:
            row['unit'] = {'baseUnit': by_id[row['id']]['saleUnit'],
                           'saleUnit': by_id[row['id']]['saleUnit'], 'ratio': 1}
        payload['unitConversion'] = None
        result = self.post(self.owner, self.path + '/preview', payload)
        self.assertEqual(result.status_code, 409, result.content)

    def test_whitespace_config_keys_normalize_safely(self):
        payload = self.converted_payload()
        payload['unitConversion']['baseOptionKey'] = ' bottle '
        payload['unitConversion']['axisKey'] = ' unit '
        result = self.post(self.owner, '/api/v1/admin/products', payload)
        self.assertEqual(result.status_code, 201, result.content)

    def test_draft_document_without_balance_blocks_reconfiguration(self):
        from inventory.models import InventoryBalance
        import uuid
        import json
        detail = self.create_converted()
        sku = next(row for row in detail['skus'] if row['skuCode'] == '53-case')
        warehouse = self.post(self.owner, '/api/v1/admin/warehouses', {
            'code': 'DRAFT', 'name': '草稿仓', 'isDefault': True}).json()['data']
        result = self.owner.post('/api/v1/admin/inventory/inbounds',
            data=json.dumps({'warehouseId': warehouse['warehouseId'], 'reason': '草稿',
                                        'items': [{'skuId': sku['skuId'], 'quantity': 1, 'unit': 'SALE'}]}),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.owner.cookies['csrftoken'].value,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(result.status_code, 201, result.content)
        self.assertFalse(InventoryBalance.objects.filter(sku__product_id=detail['productId']).exists())
        payload = self.edit()
        next(row for row in payload['unitConversion']['ratios'] if row['ratio'] != 1)['ratio'] = 7
        result = self.post(self.owner, self.path + '/preview', payload)
        self.assertEqual(result.status_code, 409, result.content)
