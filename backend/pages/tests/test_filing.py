import json
from django.test import TestCase
from accounts.models import AdminReadQuota
from catalog.tests.test_asset_references import AssetFixture


class FilingConfigTests(AssetFixture, TestCase):
    def put(self, payload, client=None):
        client = client or self.client
        return client.put('/api/v1/admin/storefront/filing', data=json.dumps(payload),content_type='application/json',HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)

    def test_save_clear_revision_and_invalid_payload(self):
        get = self.client.get('/api/v1/admin/storefront/filing')
        revision = get.json()['data']['revision']
        payload = {'expectedRevision':revision,'recordNo':'粤ICP备测试号'}
        result = self.put(payload)
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.put(payload).status_code,409)
        self.assertEqual(self.put({'expectedRevision':revision+1,'recordNo':''}).status_code,200)
        for body in [{}, {'expectedRevision':True,'recordNo':''}, {'expectedRevision':1,'recordNo':'x'*101}, {'expectedRevision':1,'recordNo':'x\n'}]:
            self.assertEqual(self.put(body).status_code,400)
        self.assertEqual(self.client.post('/api/v1/admin/storefront/filing',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value).status_code,405)

    def test_denied_access_and_account_quota(self):
        _, _, denied = self.staff(['catalog.read'])
        self.assertEqual(denied.get('/api/v1/admin/storefront/filing').status_code,403)
        self.assertEqual(self.put({'expectedRevision':1,'recordNo':'test'},denied).status_code,403)
        self.client.get('/api/v1/admin/storefront/filing')
        AdminReadQuota.objects.filter(actor=self.owner, scope='storefront.filing').update(count=60)
        result = self.client.get('/api/v1/admin/storefront/filing')
        self.assertEqual(result.status_code,429)
        self.assertIn('Retry-After',result)
