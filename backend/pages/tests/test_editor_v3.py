from copy import deepcopy
from unittest.mock import patch
from django.test import SimpleTestCase, Client, override_settings
from pages.validation import validate_config, referenced_assets, page_links, PageConfigError
from . import test_micro_flow


def config_v3():
    return {'schemaVersion': 3, 'pageType': 'MICRO', 'theme': {
        'pageBackgroundColor': '#FFFFFF', 'headerBackgroundColor': '#FFFFFF', 'brandTextColor': '#20252A'},
        'metadata': {'tags': ['首页'], 'share': {'title': '分享标题', 'description': '说明', 'coverAssetId': ''}},
        'components': [{'componentId': 'mosaic', 'type': 'MOSAIC', 'sortOrder': 0, 'visible': True,
                        'props': {'template': 'TWO', 'items': [{'assetId': ''}, {'assetId': ''}], 'gap': 8}}]}


class EditorV3ValidationTests(SimpleTestCase):
    def test_v3_draft_and_strict_old_versions(self):
        config = config_v3()
        self.assertEqual(validate_config(config, page_type='MICRO'), config)
        for version in (1, 2):
            with self.assertRaises(PageConfigError):
                validate_config({**config, 'schemaVersion': version}, page_type='MICRO')

    def test_templates_are_valid_independent_drafts(self):
        from pages.templates import template_catalog
        first = template_catalog()
        self.assertEqual(len(first['templates']), 3)
        self.assertEqual(len(first['combinations']), 3)
        for item in first['templates']:
            validate_config(item['config'], page_type='MICRO')
        first['templates'][0]['config']['components'].clear()
        self.assertTrue(template_catalog()['templates'][0]['config']['components'])

    def test_v3_invalid_slot_count_gap_and_tags(self):
        for change in ({'items': []}, {'gap': True}, {'template': 'UNKNOWN'}):
            config = config_v3()
            config['components'][0]['props'].update(change)
            with self.assertRaises(PageConfigError):
                validate_config(config, page_type='MICRO')
        for tags in (['a', 'a'], [' a '], [''], ['a'] * 6):
            config = config_v3()
            config['metadata']['tags'] = tags
            with self.assertRaises(PageConfigError):
                validate_config(config, page_type='MICRO')


    def test_mosaic_links_cover_and_publish_validation(self):
        asset = '11111111-1111-4111-8111-111111111111'
        target = '22222222-2222-4222-8222-222222222222'
        config = config_v3()
        config['metadata']['share']['coverAssetId'] = asset
        for template, slots in [('TWO', 2), ('THREE', 3), ('FOUR', 4), ('FEATURED', 3)]:
            config['components'][0]['props'] = {'template': template, 'gap': 0,
                'items': [{'assetId': asset, 'title': '', 'link': {'type': 'FUNCTION', 'targetId': 'SEARCH'}} for _ in range(slots)]}
            with patch('pages.validation.page_image_is_available', return_value=True):
                validate_config(config, page_type='MICRO', publishing=True)
        self.assertEqual(referenced_assets(config), {asset})
        config['components'][0]['visible'] = False
        self.assertEqual(referenced_assets(config, visible_only=True), {asset})
        config['components'][0]['props']['items'][0]['link'] = {'type': 'PAGE', 'targetId': target}
        self.assertEqual(list(page_links(config)), [target])
        self.assertEqual(list(page_links(config, visible_only=True)), [])
        with patch('pages.validation.page_image_is_available', return_value=False), self.assertRaises(PageConfigError):
            validate_config(config, page_type='MICRO', publishing=True)

    def test_metadata_and_spacer_invalid_values(self):
        invalid = [('tags', ['x' * 21]), ('share', {'title': 'x' * 61, 'description': '', 'coverAssetId': ''}),
                   ('share', {'title': '', 'description': 'x' * 121, 'coverAssetId': ''}),
                   ('share', {'title': '', 'description': '', 'coverAssetId': 'not-id'}), ('tags', 'x')]
        for key, value in invalid:
            config = config_v3()
            config['metadata'][key] = value
            with self.subTest(key=key), self.assertRaises(PageConfigError):
                validate_config(config, page_type='MICRO')
        config = config_v3()
        config['components'][0] = {**config['components'][0], 'type': 'SPACER', 'props': {'height': True}}
        with self.assertRaises(PageConfigError):
            validate_config(config, page_type='MICRO')


class EditorV3ApiTests(test_micro_flow.MicroFlowTests):
    def test_search_tags_templates_capabilities_and_share(self):
        page = self.create('品牌首页')
        config = config_v3()
        config['components'] = [{'componentId': 'space', 'type': 'SPACER', 'sortOrder': 0,
                                 'visible': True, 'props': {'height': 24}}]
        result = self.save(page, config)
        self.assertEqual(result.status_code, 200, result.content)
        page = result.json()['data']
        self.create('其他页')
        listing = self.client.get(test_micro_flow.PAGES + '?q=品牌&tag=首页')
        self.assertEqual(listing.json()['data']['total'], 1)
        self.assertEqual(listing.json()['data']['rows'][0]['tags'], ['首页'])
        self.assertEqual(self.client.get(test_micro_flow.PAGES + '/templates').status_code, 200)
        self.assertEqual(self.client.get(test_micro_flow.PAGES + '/capabilities').json()['data']['supportedSchemaVersions'], [1, 2, 3, 4])
        preview = self.send(self.client, 'post', f"{test_micro_flow.PAGES}/{page['pageId']}/preview", {'expectedRevision': page['revision']})
        self.assertEqual(preview.json()['data']['share']['title'], '分享标题')
        self.assertEqual(self.publish(page).json()['error']['code'], 'PAGE_RUNTIME_UNSUPPORTED')
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=3):
            self.assertEqual(self.publish(page, key='v3-publish-key').status_code, 200)
        path = f"/api/v1/app/pages/{page['pageId']}"
        self.assertEqual(Client().get(path + '?schemaVersion=2').status_code, 422)
        self.assertEqual(Client().get(path + '?schemaVersion=3').json()['data']['share']['description'], '说明')

    def test_share_cover_is_retained_private_until_published_and_copied(self):
        import uuid
        from catalog.models import Asset
        from pages.models import PageDraftAsset
        identifier = uuid.uuid4()
        path = self.media_root / f'page/{identifier}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'test-image')
        Asset.objects.create(id=identifier, kind='IMAGE', content_type='image/png', byte_size=10,
            width=1, height=1, sha256='0' * 64, original_name='cover.png',
            stored_name=f'page/{identifier}.png', created_by=self.owner)
        page = self.create()
        config = config_v3()
        config['components'] = []
        config['metadata']['share']['coverAssetId'] = str(identifier)
        page = self.save(page, config).json()['data']
        self.assertTrue(PageDraftAsset.objects.filter(page_id=page['pageId'], asset_id=identifier).exists())
        public_asset = f'/api/v1/app/assets/{identifier}/file'
        self.assertEqual(Client().get(public_asset).status_code, 404)
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=3):
            self.assertEqual(self.publish(page, key='cover-v3-publish-key').status_code, 200)
        result = Client().get(f"/api/v1/app/pages/{page['pageId']}?schemaVersion=3").json()['data']
        self.assertEqual(result['share']['coverUrl'], public_asset)
        self.assertNotIn('metadata', result['config'])
        self.assertNotIn('首页', __import__('json').dumps(result, ensure_ascii=False))
        self.assertEqual(Client().get(public_asset).status_code, 200)
        copied = self.send(self.client, 'post', f"{test_micro_flow.PAGES}/{page['pageId']}/copy", {
            'name': '封面副本', 'source': 'PUBLISHED', 'expectedRevision': page['revision']}, key='cover-v3-copy-key')
        self.assertEqual(copied.status_code, 201, copied.content)
        self.assertEqual(copied.json()['data']['config']['metadata'], config['metadata'])
        changed = deepcopy(config)
        changed['metadata']['share']['title'] = '未发布的标题'
        self.save(page, changed)
        self.assertEqual(Client().get(f"/api/v1/app/pages/{page['pageId']}?schemaVersion=3").json()['data']['share']['title'], '分享标题')
