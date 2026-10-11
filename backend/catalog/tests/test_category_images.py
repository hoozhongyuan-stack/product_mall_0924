import json
from stores.tests.test_finance_api import FinanceApiTests


class CategoryImageTests(FinanceApiTests):
    def test_wrong_depth_media_and_nonexistent_assets_rejected(self):
        result = self.post('/api/v1/admin/categories', {'name': '分类', 'bannerAssetId': '00000000-0000-0000-0000-000000000001'})
        self.assertEqual(result.status_code, 400)
        result = self.post('/api/v1/admin/categories', {'name': '分类', 'imageAssetId': '00000000-0000-0000-0000-000000000001'})
        self.assertEqual(result.status_code, 400)

    def test_filing_saved_and_public(self):
        draft = self.client.get('/api/v1/admin/storefront/filing')
        self.assertEqual(draft.status_code, 200)
        revision = draft.json()['data']['revision']
        result = self.client.put('/api/v1/admin/storefront/filing', data=json.dumps({'expectedRevision': revision, 'recordNo': '粤ICP备测试号'}), content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.client.get('/api/v1/app/storefront').json()['data']['filing']['recordNo'], '粤ICP备测试号')


from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from catalog.tests.test_asset_references import AssetFixture
from catalog.tests.test_media_flow import png
from catalog.models import Category
from catalog.media import orphan_assets


class CategoryUploadTests(AssetFixture, TestCase):
    def upload(self, width, height):
        result = self.client.post('/api/v1/admin/assets', {'kind': 'IMAGE', 'file': SimpleUploadedFile('category.png', png(width,height), content_type='image/png')}, HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(result.status_code, 201, result.content)
        return result.json()['data']['assetId']

    def post_category(self, body):
        return self.client.post('/api/v1/admin/categories', data=json.dumps(body), content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def test_square_icon_landscape_banner_visible_retained_and_inactive_private(self):
        square = self.upload(8,8)
        wide = self.upload(24,8)
        root = self.post_category({'name': '礼盒', 'imageAssetId': square})
        self.assertEqual(root.status_code, 201, root.content)
        root_id = root.json()['data']['id']
        child = self.post_category({'name': '商务礼盒', 'parentId': root_id, 'bannerAssetId': wide})
        self.assertEqual(child.status_code, 201, child.content)
        self.assertFalse(orphan_assets().filter(pk__in=[square,wide]).exists())
        public = self.client.get('/api/v1/app/categories').json()['data']
        self.assertTrue(public[0]['imageUrl'].endswith('/file'))
        self.assertTrue(public[1]['bannerImageUrl'].endswith('/file'))
        self.assertNotIn('image', public[0])
        self.assertEqual(self.client.get(f'/api/v1/app/assets/{wide}/file').status_code, 200)
        response = self.client.patch(f'/api/v1/admin/categories/{root_id}', data=json.dumps({'expectedRevision': 1,'status':'INACTIVE'}), content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/app/assets/{wide}/file').status_code, 404)
        self.assertFalse(orphan_assets().filter(pk=wide).exists())

    def test_wrong_aspect_and_missing_asset_rejected(self):
        wide = self.upload(24,8)
        self.assertEqual(self.post_category({'name':'错误图','imageAssetId':wide}).status_code, 400)
        root = Category.objects.create(name='一级')
        square = self.upload(8,8)
        self.assertEqual(self.post_category({'name':'错误横幅','parentId':str(root.pk),'bannerAssetId':square}).status_code, 400)
        root_id = str(root.pk)
        stale = self.client.patch(f'/api/v1/admin/categories/{root_id}',data=json.dumps({'expectedRevision':2,'imageAssetId':square}),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(stale.status_code,409)

    def test_media_binding_requires_upload_or_read_and_preserves_retained_permissions(self):
        image = self.upload(8,8)
        _, _, client = self.staff(['catalog.read','catalog.write'])
        result = client.post('/api/v1/admin/categories', data=json.dumps({'name':'越权图','imageAssetId':image}), content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(result.status_code,403)
        root = self.post_category({'name':'保留图片','imageAssetId':image}).json()['data']
        result = client.patch('/api/v1/admin/categories/'+root['id'], data=json.dumps({'expectedRevision':1,'name':'保留图片改名','imageAssetId':image}), content_type='application/json',HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(result.status_code,200)
        self.assertEqual(client.get('/api/v1/admin/assets/'+image+'/file').status_code,200)

    def test_category_clear_and_unsupported_media(self):
        from catalog.models import Asset
        image = self.upload(8,8)
        root = self.post_category({'name':'清空分类','imageAssetId':image}).json()['data']
        response = self.client.patch('/api/v1/admin/categories/'+root['id'],data=json.dumps({'expectedRevision':1,'imageAssetId':None}),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.json()['data']['image'])
        self.assertTrue(orphan_assets().filter(pk=image).exists())
        Asset.objects.filter(pk=image).update(kind='VIDEO')
        self.assertEqual(self.post_category({'name':'错误媒体','imageAssetId':image}).status_code,400)
