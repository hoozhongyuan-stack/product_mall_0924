import hashlib
import json
from stores.tests.test_profit_batch import ProfitBatchTests
from stores.models import StoreSkuProfitRule
from catalog.models import Sku


class ProductProfitTests(ProfitBatchTests):
    def test_product_share_and_sku_cost_are_independent(self):
        share = {'items': [{'productId': str(self.skus[0].product_id), 'expectedRevision': 0}], 'platformShareBps': 2500}
        target = hashlib.sha256(json.dumps([2500, [[share['items'][0]['productId'], 0]]], separators=(',', ':')).encode()).hexdigest()
        response = self.post('/api/v1/admin/stores/platform-shares', share, HTTP_X_ACTION_CONFIRMATION=self.confirm('stores.share.batch.configure', target, 0))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(StoreSkuProfitRule.objects.count(), 0)
        costs = {'items': [{'skuId': str(self.skus[0].pk), 'expectedRevision': 0, 'purchaseCostFen': 6000, 'expectedUnitVersionId': str(self.skus[0].current_unit_id)}]}
        target = hashlib.sha256(json.dumps([[str(self.skus[0].pk), 0, 6000, str(self.skus[0].current_unit_id)]], separators=(',', ':')).encode()).hexdigest()
        response = self.post('/api/v1/admin/stores/purchase-costs', costs, HTTP_X_ACTION_CONFIRMATION=self.confirm('stores.cost.batch.configure', target, 0))
        self.assertEqual(response.status_code, 200, response.content)
        listing = self.client.get('/api/v1/admin/stores/product-profit-rules?search=BATCH-P&pageSize=1').json()['data']
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['items'][0]['platformShareBps'], 2500)
        self.assertEqual(listing['items'][0]['shareRevision'], 1)
        self.assertEqual(listing['items'][0]['skus'][0]['purchaseCostFen'], 6000)

    def test_product_share_batch_requires_confirmation(self):
        result = self.post('/api/v1/admin/stores/platform-shares', {'items': [{'productId': str(self.skus[0].product_id), 'expectedRevision': 0}], 'platformShareBps': 2500})
        self.assertEqual(result.status_code, 403)

    def test_cost_batch_is_atomic_and_confirmation_payload_bound(self):
        costs = {'items': [{'skuId': str(sku.pk), 'expectedRevision': 0, 'purchaseCostFen': 6000, 'expectedUnitVersionId': str(sku.current_unit_id)} for sku in self.skus]}
        payload = sorted([[row['skuId'], row['expectedRevision'], row['purchaseCostFen'], row['expectedUnitVersionId']] for row in costs['items']])
        target = hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
        token = self.confirm('stores.cost.batch.configure', target, 0)
        changed = {'items': [{**costs['items'][0], 'purchaseCostFen': 6100}, *costs['items'][1:]]}
        self.assertEqual(self.post('/api/v1/admin/stores/purchase-costs', changed, HTTP_X_ACTION_CONFIRMATION=token).status_code, 403)
        stale = {'items': [{**costs['items'][0], 'expectedRevision': 1}, *costs['items'][1:]]}
        self.assertEqual(self.post('/api/v1/admin/stores/purchase-costs', stale).status_code, 409)
        self.assertEqual(StoreSkuProfitRule.objects.count(), 0)

    def test_conflicting_legacy_share_requires_product_confirmation(self):
        from stores.profit_access import effective_share
        for index, sku in enumerate(self.skus[:2]):
            StoreSkuProfitRule.objects.create(sku=sku, unit_version=sku.current_unit, purchase_cost_fen=100, platform_share_bps=1000+index*1000)
        ratio, revision, source = effective_share(self.skus[0].product_id)
        self.assertIsNone(ratio)
        self.assertEqual(source, 'LEGACY_CONFLICT')
        data = self.client.get('/api/v1/admin/stores/product-profit-rules?configuration=NEEDS_CONFIRMATION').json()['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['shareSource'], 'LEGACY_CONFLICT')

    def test_invalid_independent_payload_is_rejected(self):
        for payload in [{'items': []}, {'items': [{'skuId': 'invalid', 'expectedRevision': 0, 'purchaseCostFen': 1}]},
                        {'items': [{'skuId': str(self.skus[0].pk), 'expectedRevision': False, 'purchaseCostFen': 1}]},
                        {'items': [{'skuId': str(self.skus[0].pk), 'expectedRevision': 0, 'purchaseCostFen': -1}]}]:
            self.assertEqual(self.post('/api/v1/admin/stores/purchase-costs', payload).status_code, 400)
        for query in ['page=0', 'pageSize=101', 'categoryId=bad', 'configuration=INVALID']:
            self.assertEqual(self.client.get('/api/v1/admin/stores/product-profit-rules?'+query).status_code, 400)

    def test_cost_edit_preserves_existing_legacy_ratio_and_filter_states(self):
        from stores.models import StoreProductProfitRule
        sku = self.skus[0]
        StoreSkuProfitRule.objects.create(sku=sku, unit_version=sku.current_unit, purchase_cost_fen=100, platform_share_bps=2000)
        body = {'items':[{'skuId':str(sku.pk),'expectedRevision':1,'purchaseCostFen':200,'expectedUnitVersionId':str(sku.current_unit_id)}]}
        target = hashlib.sha256(json.dumps([[str(sku.pk),1,200,str(sku.current_unit_id)]],separators=(',',':')).encode()).hexdigest()
        result = self.post('/api/v1/admin/stores/purchase-costs',body,HTTP_X_ACTION_CONFIRMATION=self.confirm('stores.cost.batch.configure',target,0))
        self.assertEqual(result.status_code,200)
        self.assertEqual(StoreSkuProfitRule.objects.get(pk=sku.pk).platform_share_bps,2000)
        product = self.client.get('/api/v1/admin/stores/product-profit-rules?search=BATCH-P').json()['data']['items'][0]
        self.assertEqual(product['shareSource'],'LEGACY_UNIFORM')
        StoreProductProfitRule.objects.create(product=sku.product, platform_share_bps=3000)
        for query in ['configuration=CONFIGURED','categoryId='+str(sku.product.category_id)]:
            self.assertEqual(self.client.get('/api/v1/admin/stores/product-profit-rules?'+query).json()['data']['total'],1)
        self.assertEqual(self.client.get('/api/v1/admin/stores/product-profit-rules?configuration=UNCONFIGURED').json()['data']['total'],0)
        self.assertEqual(self.client.get('/api/v1/admin/stores/product-profit-rules?search='+'x'*121).status_code,400)
        old = {'expectedRevision':2,'purchaseCostFen':300,'platformShareBps':1000}
        token = self.confirm('stores.profit.configure',sku.pk,2)
        response = self.client.put('/api/v1/admin/stores/profit-rules/'+str(sku.pk),data=json.dumps(old),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(response.status_code,409)
        self.assertEqual(StoreSkuProfitRule.objects.get(pk=sku.pk).purchase_cost_fen,200)

    def test_share_edit_stale_batch_unknown_entities_units_and_methods(self):
        from stores.models import StoreProductProfitRule
        product = self.skus[0].product
        StoreProductProfitRule.objects.create(product=product, platform_share_bps=3000)
        body = {'items':[{'productId':str(product.pk),'expectedRevision':1}],'platformShareBps':4000}
        target = hashlib.sha256(json.dumps([4000,[[str(product.pk),1]]],separators=(',',':')).encode()).hexdigest()
        result = self.post('/api/v1/admin/stores/platform-shares',body,HTTP_X_ACTION_CONFIRMATION=self.confirm('stores.share.batch.configure',target,0))
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.post('/api/v1/admin/stores/platform-shares',body).status_code,409)
        self.assertEqual(self.post('/api/v1/admin/stores/platform-shares',{**body,'items':[{'productId':'00000000-0000-0000-0000-000000000001','expectedRevision':0}]}).status_code,404)
        costs = {'items':[{'skuId':str(self.skus[0].pk),'expectedRevision':0,'purchaseCostFen':100,'expectedUnitVersionId':None}]}
        self.skus[0].current_unit = None
        self.skus[0].save()
        self.assertEqual(self.post('/api/v1/admin/stores/purchase-costs',costs).status_code,400)
        self.assertEqual(self.client.get('/api/v1/admin/stores/purchase-costs').status_code,405)
        self.assertEqual(self.post('/api/v1/admin/stores/product-profit-rules',{}).status_code,405)
        for bad in [{**body,'platformShareBps':True},{**body,'platformShareBps':10001},{**body,'items':[body['items'][0]]*2}, {**body,'items':[{'productId':None,'expectedRevision':0}]}]:
            self.assertEqual(self.post('/api/v1/admin/stores/platform-shares',bad).status_code,400)

    def test_concurrent_unit_change_never_binds_draft_cost_to_new_unit(self):
        from catalog.models import SkuUnitVersion
        sku = self.skus[0]
        original = str(sku.current_unit_id)
        changed = SkuUnitVersion.objects.create(sku=sku,base_unit='瓶',sale_unit='箱',ratio=6)
        Sku.objects.filter(pk=sku.pk).update(current_unit=changed)
        body = {'items':[{'skuId':str(sku.pk),'expectedRevision':0,'purchaseCostFen':100,'expectedUnitVersionId':original}]}
        response = self.post('/api/v1/admin/stores/purchase-costs',body)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['error']['code'],'UNIT_REVISION_CONFLICT')
        self.assertFalse(StoreSkuProfitRule.objects.filter(pk=sku.pk).exists())
        for unit in [False, 'not-a-uuid']:
            response = self.post('/api/v1/admin/stores/purchase-costs',{'items':[{**body['items'][0],'expectedUnitVersionId':unit}]})
            self.assertEqual(response.status_code,400)
