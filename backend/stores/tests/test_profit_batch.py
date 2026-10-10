import hashlib
import json

from catalog.models import Category, Product, Sku, SkuUnitVersion
from stores.models import StoreSkuProfitRule
from stores.tests.test_finance_api import FinanceApiTests


class ProfitBatchTests(FinanceApiTests):
    def setUp(self):
        super().setUp()
        product = Product.objects.create(name='批量商品', product_no='BATCH-P',
            category=Category.objects.create(name='批量类目'), fulfillment_kind='SHIP')
        self.skus = []
        for index in range(3):
            sku = Sku.objects.create(product=product, sku_code=f'BATCH-{index}',
                spec_key=str(index), list_price_fen=10000)
            unit = SkuUnitVersion.objects.create(sku=sku, base_unit='瓶', sale_unit='瓶', ratio=1)
            sku.current_unit = unit
            sku.save()
            self.skus.append(sku)
        self.batch = {'items': [{'skuId': str(sku.pk), 'expectedRevision': 0} for sku in self.skus],
                      'purchaseCostFen': 6000, 'platformShareBps': 2000}

    def token(self, body):
        payload = [body['purchaseCostFen'], body['platformShareBps'],
                   sorted([[item['skuId'], item['expectedRevision']] for item in body['items']])]
        digest = hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
        return self.confirm('stores.profit.batch.configure', digest, 0)

    def submit(self, body, token=''):
        return self.post('/api/v1/admin/stores/profit-rules', body, HTTP_X_ACTION_CONFIRMATION=token)

    def test_batch_confirms_exact_payload_and_persists_all_selected_rules(self):
        self.assertEqual(self.submit(self.batch).status_code, 403)
        token = self.token(self.batch)
        self.assertEqual(self.submit({**self.batch, 'purchaseCostFen': 6100}, token).status_code, 403)
        self.assertEqual(StoreSkuProfitRule.objects.count(), 0)
        result = self.submit(self.batch, self.token(self.batch))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(len(result.json()['data']['items']), 3)
        self.assertEqual(set(StoreSkuProfitRule.objects.values_list('revision', flat=True)), {1})
        self.assertEqual(set(StoreSkuProfitRule.objects.values_list('purchase_cost_fen', flat=True)), {6000})

    def test_stale_revision_invalid_sku_and_missing_unit_never_partially_save(self):
        for items in [
            [{**self.batch['items'][0], 'expectedRevision': 1}, *self.batch['items'][1:]],
            [self.batch['items'][0], {'skuId': '00000000-0000-0000-0000-000000000000', 'expectedRevision': 0}],
        ]:
            body = {**self.batch, 'items': items}
            result = self.submit(body, self.token(body))
            self.assertIn(result.status_code, [409, 404], result.content)
            self.assertEqual(StoreSkuProfitRule.objects.count(), 0)
        self.skus[1].current_unit = None
        self.skus[1].save()
        self.assertEqual(self.submit(self.batch, self.token(self.batch)).status_code, 400)
        self.assertEqual(StoreSkuProfitRule.objects.count(), 0)

    def test_batch_validation_is_bounded_strict_and_rejects_duplicates(self):
        for body in [
            {**self.batch, 'items': []},
            {**self.batch, 'items': [self.batch['items'][0]] * 2},
            {**self.batch, 'items': [self.batch['items'][0]] * 101},
            {**self.batch, 'items': [{'skuId': 'invalid', 'expectedRevision': 0}]},
            {**self.batch, 'items': [{**self.batch['items'][0], 'expectedRevision': True}]},
            {**self.batch, 'items': [{**self.batch['items'][0], 'extra': 1}]},
            {**self.batch, 'purchaseCostFen': -1},
            {**self.batch, 'purchaseCostFen': 9900000001},
            {**self.batch, 'purchaseCostFen': True},
            {**self.batch, 'platformShareBps': 10001},
            {**self.batch, 'extra': 1},
        ]:
            self.assertEqual(self.submit(body).status_code, 400, body)
        self.assertEqual(StoreSkuProfitRule.objects.count(), 0)

    def test_batch_updates_existing_and_new_rules_with_canonical_item_order(self):
        existing = self.skus[0]
        StoreSkuProfitRule.objects.create(sku=existing, unit_version=existing.current_unit,
            purchase_cost_fen=100, platform_share_bps=100, revision=3)
        body = {**self.batch, 'items': [{**self.batch['items'][0], 'expectedRevision': 3}, *self.batch['items'][1:]]}
        token = self.token(body)
        result = self.submit({**body, 'items': list(reversed(body['items']))}, token)
        self.assertEqual(result.status_code, 200, result.content)
        existing_rule = StoreSkuProfitRule.objects.get(pk=existing.pk)
        self.assertEqual(existing_rule.revision, 4)
        self.assertEqual(self.submit(body, token).status_code, 409)

    def test_batch_requires_session_csrf_and_manage_permission(self):
        from django.test import Client
        self.assertEqual(Client().post('/api/v1/admin/stores/profit-rules',
            data=json.dumps(self.batch), content_type='application/json').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/admin/stores/profit-rules',
            data=json.dumps(self.batch), content_type='application/json').status_code, 403)
        self.owner.kind = 'STAFF'
        self.owner.save(update_fields=['kind'])
        self.assertEqual(self.submit(self.batch).status_code, 403)

    def test_mid_batch_failure_rolls_back_rules_audits_and_confirmation(self):
        from unittest.mock import patch
        from accounts.models import AuditLog
        from stores.access import StoreError
        from accounts.security import audit
        token = self.token(self.batch)
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise StoreError('保存失败，请重试。')
            return audit(*args, **kwargs)
        with patch('stores.finance_rules.audit', side_effect=fail_second):
            self.assertEqual(self.submit(self.batch, token).status_code, 400)
        self.assertEqual(StoreSkuProfitRule.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(action_code='stores.profit.batch.configure').count(), 0)
        self.assertEqual(self.submit(self.batch, token).status_code, 200)
