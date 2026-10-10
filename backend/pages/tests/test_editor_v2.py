from copy import deepcopy
from django.test import SimpleTestCase, Client, override_settings
from pages.validation import validate_config, PageConfigError, referenced_assets, page_links
from .test_micro_flow import MicroFlowTests, PAGES


def config_v2():
    return {'schemaVersion': 2, 'pageType': 'MICRO', 'theme': {
        'pageBackgroundColor': '#FFFFFF', 'headerBackgroundColor': '#FFFFFF', 'brandTextColor': '#000000'},
        'components': [{'componentId': 'title', 'type': 'TITLE', 'sortOrder': 0, 'visible': True,
                        'props': {'text': '推荐', 'align': 'LEFT', 'size': 20}}]}


class EditorValidationTests(SimpleTestCase):
    def test_v2_title_and_appearance(self):
        config = config_v2()
        config['components'][0]['appearance'] = {'backgroundColor': '#FFFFFF', 'padding': 16, 'margin': 0, 'radius': 8}
        self.assertEqual(validate_config(config, page_type='MICRO'), config)
        old = {**config, 'schemaVersion': 1}
        with self.assertRaises(PageConfigError):
            validate_config(old, page_type='MICRO')

    def test_non_object_component_is_rejected(self):
        config = config_v2()
        config["components"] = [None]
        with self.assertRaises(PageConfigError):
            validate_config(config, page_type="MICRO")

    def test_bad_appearance_and_product_limit(self):
        config = config_v2()
        config['components'][0]['appearance'] = {'padding': 17}
        with self.assertRaises(PageConfigError):
            validate_config(config, page_type='MICRO')
        config = config_v2()
        config['components'][0] = {**config['components'][0], 'type': 'PRODUCT_LIST', 'props': {
            'source': 'MANUAL', 'productIds': [], 'categoryId': '', 'limit': 21, 'layout': 'GRID', 'sort': 'NEWEST'}}
        with self.assertRaises(PageConfigError):
            validate_config(config, page_type='MICRO')


    def test_image_navigation_and_product_props_are_strict(self):
        identifier = 'efdfc945-73b8-46de-aa12-e53d069b0a25'
        cases = [
            ('TITLE', {'text': '标题', 'subtitle': '介绍', 'align': 'CENTER', 'size': 24}, 'size', True),
            ('IMAGE', {'assetId': identifier, 'ratio': '16:9', 'link': {'type': 'FUNCTION', 'targetId': 'SEARCH'}}, 'ratio', '3:2'),
            ('NAVIGATION', {'items': [{'title': '搜索', 'assetId': identifier,
                'link': {'type': 'FUNCTION', 'targetId': 'SEARCH'}}], 'columns': 4}, 'columns', True),
            ('PRODUCT_LIST', {'source': 'CATEGORY', 'productIds': [], 'categoryId': identifier,
                'limit': 20, 'layout': 'LIST', 'sort': 'PRICE_ASC'}, 'source', 'UNKNOWN')]
        for kind, props, key, invalid in cases:
            config = config_v2()
            config['components'][0] = {**config['components'][0], 'type': kind, 'props': props}
            validate_config(config, page_type='MICRO')
            bad = deepcopy(config)
            bad['components'][0]['props'][key] = invalid
            with self.subTest(kind=kind), self.assertRaises(PageConfigError):
                validate_config(bad, page_type='MICRO')

    def test_new_assets_and_page_links_cover_hidden_and_visible_slots(self):
        asset = 'efdfc945-73b8-46de-aa12-e53d069b0a25'
        target = '1cb20837-fd29-44fc-93fe-becdf3a391cb'
        config = config_v2()
        link = {'type': 'PAGE', 'targetId': target}
        config['components'] = [
            {'componentId': 'image', 'type': 'IMAGE', 'sortOrder': 0, 'visible': True,
             'props': {'assetId': asset, 'ratio': 'AUTO', 'link': link}},
            {'componentId': 'nav', 'type': 'NAVIGATION', 'sortOrder': 1, 'visible': False,
             'props': {'items': [{'title': '隐藏', 'assetId': asset, 'link': link}], 'columns': 3}}]
        self.assertEqual(referenced_assets(config), {asset})
        self.assertEqual(referenced_assets(config, visible_only=True), {asset})
        self.assertEqual(list(page_links(config)), [target, target])
        self.assertEqual(list(page_links(config, visible_only=True)), [target])


class EditorApiTests(MicroFlowTests):
    def test_v2_draft_preview_runtime_gate_and_public_negotiation(self):
        page = self.create()
        saved = self.save(page, config_v2())
        self.assertEqual(saved.status_code, 200, saved.content)
        page = saved.json()['data']
        preview = self.send(self.client, 'post', f"{PAGES}/{page['pageId']}/preview", {'expectedRevision': page['revision']})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(self.publish(page).json()['error']['code'], 'PAGE_RUNTIME_UNSUPPORTED')
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=2):
            self.assertEqual(self.publish(page, key='v2-publish-after-gate').status_code, 200)
        path = f"/api/v1/app/pages/{page['pageId']}"
        self.assertEqual(Client().get(path).status_code, 422)
        self.assertEqual(Client().get(path + '?schemaVersion=2').status_code, 200)
        self.assertEqual(self.client.get(PAGES + '/capabilities').json()['data']['supportedSchemaVersions'], [1, 2, 3, 4])

    def test_copy_is_independent_idempotent_draft_and_conflict_safe(self):
        page = self.create()
        page = self.save(page, config_v2()).json()['data']
        path = f"{PAGES}/{page['pageId']}/copy"
        body = {'name': '副本', 'source': 'DRAFT', 'expectedRevision': page['revision']}
        result = self.send(self.client, 'post', path, body, key='copy-request-key')
        self.assertEqual(result.status_code, 201, result.content)
        copied = result.json()['data']
        self.assertNotEqual(copied['pageId'], page['pageId'])
        self.assertNotEqual(copied['config']['components'][0]['componentId'], 'title')
        self.assertIsNone(copied['publishedRevision'])
        same = self.send(self.client, 'post', path, body, key='copy-request-key')
        self.assertEqual(same.json()['data']['pageId'], copied['pageId'])
        bad = self.send(self.client, 'post', path, {**body, 'name': '其他'}, key='copy-request-key')
        self.assertEqual(bad.status_code, 409)

    def test_product_preview_strict_bounds_and_permissions(self):
        props = {'source': 'MANUAL', 'productIds': [], 'categoryId': '', 'limit': 10,
                 'layout': 'GRID', 'sort': 'NEWEST'}
        preview = self.send(self.client, 'post', PAGES + '/product-preview', {'props': props})
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()['data']['products'], [])
        self.assertEqual(self.send(self.client, 'post', PAGES + '/product-preview', {
            'props': {**props, 'limit': True}}).status_code, 400)

    def test_copy_conflict_missing_source_and_audit_atomicity(self):
        from pages.models import MicroPage, PageCopyRequest
        from unittest.mock import patch
        page = self.create()
        path = f"{PAGES}/{page['pageId']}/copy"
        body = {'name': '副本', 'source': 'DRAFT', 'expectedRevision': page['revision']}
        self.assertEqual(self.send(self.client, 'post', path, {**body, 'expectedRevision': 100},
                                   key='copy-stale-key').status_code, 409)
        self.assertEqual(self.send(self.client, 'post', path, {**body, 'source': 'PUBLISHED'},
                                   key='copy-unpublished-key').status_code, 409)
        with patch('pages.editor_views.audit', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.send(self.client, 'post', path, body, key='copy-atomic-key')
        self.assertEqual(MicroPage.objects.filter(page_type='MICRO').count(), 1)
        self.assertEqual(PageCopyRequest.objects.count(), 0)

    def test_rollback_target_checks_runtime_fence(self):
        from types import SimpleNamespace
        from pages.history import validate_target
        page = self.create()
        version = SimpleNamespace(config_json=config_v2())
        content = SimpleNamespace(id=page['pageId'], page_type='MICRO')
        with self.assertRaises(PageConfigError) as caught:
            validate_target('page', content, version)
        self.assertEqual(caught.exception.code, 'PAGE_RUNTIME_UNSUPPORTED')
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=2):
            validate_target('page', content, version)
