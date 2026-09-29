"""Product detail image identity, private binding and retained file lifecycle."""
import json
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from catalog.media import asset_path, orphan_assets, purge_expired_orphans
from catalog.models import Asset, Category, Product, Sku, SkuUnitVersion
from catalog.tests.test_asset_references import AssetFixture


class DescriptionImageTests(AssetFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.parent = Category.objects.create(name='详情图父分类')
        self.leaf = Category.objects.create(name='详情图分类', parent=self.parent)
        self.product = Product.objects.create(product_no='DETAIL-IMAGE', name='详情图片商品',
            category=self.leaf, fulfillment_kind='SHIP')

    def img(self, asset, **attrs):
        extra = ' '.join(f'{key}="{value}"' for key, value in attrs.items())
        return f'<img data-asset-id="{asset.id}" {extra}>'

    def save(self, content, client=None, revision=None):
        client = client or self.client
        self.product.refresh_from_db()
        return client.patch(f'/api/v1/admin/products/{self.product.id}', json.dumps({
            'expectedRevision': revision if revision is not None else self.product.revision,
            'descriptionHtml': content}), content_type='application/json',
            HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)

    def make_public(self):
        self.product.refresh_from_db()
        self.product.main_image = self.asset()
        self.product.status, self.product.ever_on_sale = 'ON_SALE', True
        self.product.save()
        sku = Sku.objects.create(product=self.product, sku_code='DETAIL-SKU', spec_key='single',
                                 list_price_fen=100, sale_status='ON_SALE')
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit='个', sale_unit='个', ratio=1)
        sku.current_unit = unit
        sku.save()

    def test_canonical_storage_admin_public_presentation_and_foreign_key_retention(self):
        from catalog.models import ProductDescriptionImage
        asset = self.asset()
        result = self.save('<p>说明</p>'+self.img(asset, src='https://untrusted.invalid/image', alt='示例'))
        self.assertEqual(result.status_code, 200, result.content)
        self.product.refresh_from_db()
        self.assertEqual(self.product.description_html, f'<p>说明</p><img data-asset-id="{asset.id}" alt="示例">')
        self.assertIn(f'src="/api/v1/admin/assets/{asset.id}/file"', result.json()['data']['descriptionHtml'])
        self.assertNotIn('untrusted.invalid', result.content.decode())
        self.assertTrue(ProductDescriptionImage.objects.filter(product=self.product, asset=asset).exists())
        with transaction.atomic(), self.assertRaises(ProtectedError):
            asset.delete()
        self.assertFalse(orphan_assets().filter(pk=asset.id).exists())
        self.assertEqual(self.delete(asset).status_code, 409)
        self.assertEqual(self.refs(asset)['items'][0]['role'], 'DESCRIPTION_IMAGE')
        self.assertEqual(Client().get(f'/api/v1/app/assets/{asset.id}/file').status_code, 404)
        self.make_public()
        public = Client().get(f'/api/v1/app/products/{self.product.id}')
        self.assertEqual(public.status_code, 200, public.content)
        self.assertIn(f'src="/api/v1/app/assets/{asset.id}/file"', public.json()['data']['descriptionHtml'])
        self.assertNotIn('/api/v1/admin/', public.content.decode())
        response = Client().get(f'/api/v1/app/assets/{asset.id}/file')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content))
        self.parent.status = 'INACTIVE'
        self.parent.save()
        self.assertEqual(Client().get(f'/api/v1/app/assets/{asset.id}/file').status_code, 404)

    def test_unbound_or_external_images_invalid_alt_count_and_missing_assets_are_rejected(self):
        asset = self.asset()
        cases = ['<img src="https://outside.invalid/photo">', '<img>',
                 '<img data-asset-id="bad">', '<img data-asset-id="'+str(uuid.uuid4())+'">',
                 self.img(asset, alt='x'*201), self.img(asset)*21,
                 f'<img data-asset-id="{asset.id}" data-asset-id="{uuid.uuid4()}">']
        for content in cases:
            with self.subTest(content=content[:80]):
                result = self.save(content)
                self.assertEqual(result.status_code, 400, result.content)
                self.product.refresh_from_db()
                self.assertEqual(self.product.description_html, '')
                self.assertEqual(self.product.revision, 1)

    def test_access_permissions_retain_only_same_product_and_catalog_reader_files(self):
        owner_asset = self.asset()
        actor, _, editor = self.staff(['catalog.write', 'catalog.read', 'asset.upload'])
        self.assertEqual(self.save(self.img(owner_asset), editor).status_code, 403)
        own = self.asset(actor)
        first = self.save(self.img(own), editor)
        self.assertEqual(first.status_code, 200, first.content)
        _, _, reader = self.staff(['catalog.read'])
        file = reader.get(f'/api/v1/admin/assets/{own.id}/file')
        self.assertEqual(file.status_code, 200)
        self.assertTrue(b"".join(file.streaming_content))
        self.assertEqual(reader.get(f'/api/v1/admin/assets/{owner_asset.id}/file').status_code, 403)
        _, _, retained_editor = self.staff(['catalog.write'])
        self.assertEqual(self.save(first.json()['data']['descriptionHtml'], retained_editor).status_code, 200)
        self.assertEqual(self.save(self.img(owner_asset), retained_editor).status_code, 403)

    def test_replacement_removes_old_reference_then_orphan_cleanup_reclaims_only_removed_image(self):
        old, current = self.asset(), self.asset()
        self.assertEqual(self.save(self.img(old)).status_code, 200)
        Asset.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(days=2))
        self.assertEqual(purge_expired_orphans(), 0)
        self.assertEqual(self.save(self.img(current)).status_code, 200)
        self.assertTrue(orphan_assets().filter(pk=old.pk).exists())
        self.assertFalse(orphan_assets().filter(pk=current.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(purge_expired_orphans(), 1)
        self.assertFalse(asset_path(old).exists())
        self.assertTrue(asset_path(current).exists())

    def test_duplicate_nodes_keep_one_reference_and_unsafe_attributes_never_survive(self):
        from catalog.models import ProductDescriptionImage
        asset = self.asset()
        result = self.save('<script>alert(1)</script><p class="x">安全</p>'+self.img(asset,
            src='javascript:alert(1)', onerror='alert(1)', srcset='https://outside.invalid/a', style='position:fixed')*20)
        self.assertEqual(result.status_code, 200, result.content)
        html = result.json()['data']['descriptionHtml']
        self.assertEqual(html.count('<img '), 20)
        self.assertEqual(ProductDescriptionImage.objects.filter(product=self.product).count(), 1)
        for value in ['script', 'onerror', 'srcset', 'style=', 'outside.invalid', 'alert(1)', 'class=']:
            self.assertNotIn(value, html)
        self.assertEqual(self.save(html).status_code, 200)

    def test_file_kind_expiry_and_failed_revision_keep_existing_description(self):
        asset = self.asset()
        self.assertEqual(self.save(self.img(asset)).status_code, 200)
        replacement = self.asset()
        conflict = self.save(self.img(replacement), revision=1)
        self.assertEqual(conflict.status_code, 409)
        Asset.objects.filter(pk=replacement.pk).update(kind='VIDEO')
        self.assertEqual(self.save(self.img(replacement)).status_code, 400)
        Asset.objects.filter(pk=replacement.pk).update(kind='IMAGE', created_at=timezone.now()-timedelta(days=2))
        self.assertEqual(self.save(self.img(replacement)).status_code, 400)
        Asset.objects.filter(pk=replacement.pk).update(created_at=timezone.now())
        asset_path(replacement).unlink()
        self.assertEqual(self.save(self.img(replacement)).status_code, 400)
        self.product.refresh_from_db()
        self.assertIn(str(asset.id), self.product.description_html)

    def test_create_binds_description_images_atomically(self):
        from catalog.models import ProductDescriptionImage
        asset = self.asset()
        payload = {'productNo': 'WITH-DETAIL', 'name': '创建详情图', 'categoryId': str(self.leaf.id),
                   'fulfillmentKind': 'SHIP', 'descriptionHtml': self.img(asset), 'specAxes': [], 'skus': [{
                       'skuCode': 'WITH-DETAIL-SKU', 'specOptionKeys': [], 'listPriceFen': 100,
                       'saleStatus': 'OFF_SALE', 'gradePrices': [],
                       'unit': {'baseUnit': '个', 'saleUnit': '个', 'ratio': 1}}]}
        result = self.client.post('/api/v1/admin/products', json.dumps(payload), content_type='application/json',
                                  HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.assertEqual(result.status_code, 201, result.content)
        self.assertTrue(ProductDescriptionImage.objects.filter(product_id=result.json()['data']['productId'], asset=asset).exists())

    def test_description_limit_counts_canonical_html_and_roundtrips_projected_urls(self):
        asset = self.asset()
        image = f'<img data-asset-id="{asset.id}">'
        canonical = 'x'*(20000-len(image)) + image
        first = self.save(canonical)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(self.save(first.json()['data']['descriptionHtml']).status_code, 200)
        self.assertEqual(self.save(canonical+'x').status_code, 400)
        self.assertEqual(self.save(None).status_code, 400)

    def test_escaped_alt_self_closing_images_and_description_only_reference_masking(self):
        asset = self.asset()
        result = self.save(f'<p>文字<br/></p><img data-asset-id="{asset.id}" alt="&lt;test&gt;&quot;&amp;"/>')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIn('alt="&lt;test&gt;&quot;&amp;"', result.json()['data']['descriptionHtml'])
        _, _, reader = self.staff(['asset.read'])
        refs = reader.get(f'/api/v1/admin/assets/{asset.id}/references').json()['data']['items']
        self.assertEqual(refs[0]['role'], 'DESCRIPTION_IMAGE')
        self.assertIsNone(refs[0]['objectId'])
        self.assertEqual(refs[0]['label'], '引用受权限保护')
        self.assertEqual(self.save(f'<img data-asset-id="{asset.id}" alt alt="x">').status_code, 400)
        self.assertEqual(self.save(f'<img data-asset-id="{asset.id}" alt>').status_code, 400)

    def test_failed_create_leaves_no_product_or_description_reference(self):
        from catalog.models import ProductDescriptionImage
        asset = self.asset()
        _, _, writer = self.staff(['catalog.write', 'sku.price.write', 'sku.status.write', 'sku.unit.write'])
        payload = {'productNo': 'FAILED-DETAIL', 'name': '拒绝越权', 'categoryId': str(self.leaf.id),
                   'fulfillmentKind': 'SHIP', 'descriptionHtml': self.img(asset), 'specAxes': [], 'skus': [{
                       'skuCode': 'FAILED-DETAIL-SKU', 'specOptionKeys': [], 'listPriceFen': 100,
                       'saleStatus': 'OFF_SALE', 'gradePrices': [],
                       'unit': {'baseUnit': '个', 'saleUnit': '个', 'ratio': 1}}]}
        result = writer.post('/api/v1/admin/products', json.dumps(payload), content_type='application/json',
                             HTTP_X_CSRFTOKEN=writer.cookies['csrftoken'].value)
        self.assertEqual(result.status_code, 403, result.content)
        self.assertFalse(Product.objects.filter(product_no='FAILED-DETAIL').exists())
        self.assertFalse(ProductDescriptionImage.objects.filter(asset=asset).exists())


class DescriptionImageConcurrencyTests(AssetFixture, TransactionTestCase):
    def test_asset_cleanup_waits_for_product_reference_commit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from unittest.mock import patch
        from django.db import connections
        from django.test import override_settings
        from catalog.media import resolve_media
        from catalog.models import ProductDescriptionImage
        parent = Category.objects.create(name='并发父分类')
        leaf = Category.objects.create(name='并发分类', parent=parent)
        product = Product.objects.create(product_no='RACE-DESCRIPTION', name='并发详情',
            category=leaf, fulfillment_kind='SHIP')
        asset = self.asset()
        Asset.objects.filter(pk=asset.pk).update(created_at=timezone.now()-timedelta(days=2))
        locked, release, cleaning, cleaned = Event(), Event(), Event(), Event()

        def gated_media(*args, **kwargs):
            result = resolve_media(*args, **kwargs)
            locked.set()
            if not release.wait(10):
                raise RuntimeError('detail image lock gate timed out')
            return result

        def bind():
            try:
                return self.client.patch(f'/api/v1/admin/products/{product.id}', json.dumps({
                    'expectedRevision': 1, 'descriptionHtml': f'<img data-asset-id="{asset.id}">'}),
                    content_type='application/json', HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
            finally:
                connections.close_all()

        def clean():
            try:
                cleaning.set()
                return purge_expired_orphans()
            finally:
                cleaned.set()
                connections.close_all()

        with override_settings(PRODUCT_MEDIA_ORPHAN_TTL=timedelta(days=3)), \
                patch('catalog.views.resolve_media', side_effect=gated_media), ThreadPoolExecutor(max_workers=2) as pool:
            binding = pool.submit(bind)
            try:
                self.assertTrue(locked.wait(10))
                with override_settings(PRODUCT_MEDIA_ORPHAN_TTL=timedelta(days=1)):
                    cleanup = pool.submit(clean)
                    self.assertTrue(cleaning.wait(10))
                    self.assertFalse(cleaned.wait(.3), 'cleanup must wait for the asset lock')
                    release.set()
                    result, removed = binding.result(timeout=15), cleanup.result(timeout=15)
            finally:
                release.set()
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(removed, 0)
        self.assertTrue(ProductDescriptionImage.objects.filter(product=product, asset=asset).exists())
        self.assertTrue(asset_path(asset).is_file())


class DescriptionSanitizerTests(SimpleTestCase):
    def test_direct_service_input_is_bounded_and_old_plain_html_remains_compatible(self):
        from catalog.description import parse_description
        from catalog.validation import CatalogError, description
        with self.assertRaises(CatalogError):
            parse_description('<script>'+('x'*262144)+'</script>')
        self.assertEqual(description('<p class="x">说明<strong>重点'), '<p>说明<strong>重点</strong></p>')
        self.assertEqual(description('<style>body{}</style><iframe>unsafe</iframe><p>正文</p>'), '<p>正文</p>')
