from datetime import timedelta
import uuid
from django.test import SimpleTestCase, Client, override_settings
from django.utils import timezone
from pages.validation import validate_config, PageConfigError
from . import test_micro_flow
from .test_editor_v3 import config_v3
from benefits.models import CouponCampaign


def coupon_props(ids=(), source='MANUAL'):
    return {'source': source, 'campaignIds': list(ids), 'limit': 10, 'layout': 'LIST'}


def config_v4(ids=()):
    config = config_v3()
    config['schemaVersion'] = 4
    config['components'] = [{'componentId': 'coupons', 'type': 'COUPON_LIST', 'visible': True,
                             'sortOrder': 0, 'props': coupon_props(ids)}]
    return config


class CouponValidationTests(SimpleTestCase):
    def test_versioned_coupon_props(self):
        config = config_v4()
        validate_config(config, page_type='MICRO')
        for version in (1, 2, 3):
            with self.assertRaises(PageConfigError):
                validate_config({**config, 'schemaVersion': version}, page_type='MICRO')
        for key, value in [('limit', True), ('source', 'UNKNOWN'), ('campaignIds', ['invalid']), ('layout', 'GRID')]:
            config = config_v4()
            config['components'][0]['props'][key] = value
            with self.assertRaises(PageConfigError):
                validate_config(config, page_type='MICRO')


class CouponPageTests(test_micro_flow.MicroFlowTests):
    def campaign(self, suffix, **changes):
        now = timezone.now()
        return CouponCampaign.objects.create(**{'code': 'PAGE-' + suffix, 'title': '券-' + suffix,
            'kind': 'CASH', 'discount_fen': 100, 'valid_from': now - timedelta(days=1),
            'valid_until': now + timedelta(days=1), 'status': 'PUBLISHED', 'total_quantity': 5,
            'issuance_enabled': True, 'claim_mode': 'SELF', **changes})

    def test_guest_version_bound_safe_state_and_private_cache(self):
        available = self.campaign('ok')
        sold = self.campaign('sold', issued_quantity=5)
        expired = self.campaign('expired', valid_from=timezone.now()-timedelta(days=2), valid_until=timezone.now()-timedelta(days=1))
        hidden = self.campaign('draft', status='DRAFT')
        page = self.create()
        result = self.save(page, config_v4([str(row.id) for row in (available, sold, expired)]))
        self.assertEqual(result.status_code, 200, result.content)
        page = result.json()['data']
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=4):
            published = self.publish(page, key='coupon-page-publish').json()['data']
        path = f"/api/v1/app/pages/{page['pageId']}/coupons?versionId={published['versionId']}"
        result = Client().get(path)
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()['data']
        self.assertIsNone(data['memberId'])
        self.assertEqual([row['claimState'] for row in data['componentData']['coupons']], ['GUEST', 'SOLD_OUT', 'EXPIRED'])
        self.assertNotIn('code', data['componentData']['coupons'][0])
        self.assertFalse(data['componentData']['coupons'][0]['canClaim'])
        self.assertIn('no-store', result['Cache-Control'])
        self.assertIn('Authorization', result['Vary'])
        self.assertEqual(Client().get(path, HTTP_AUTHORIZATION='invalid').status_code, 401)
        self.assertEqual(Client().get(path.replace(published['versionId'], str(uuid.uuid4()))).status_code, 409)

    def test_release_report_collects_refs_runtime_and_conflicts(self):
        from pages.models import MicroPage
        page = self.create()
        config = config_v4([str(uuid.uuid4()), str(uuid.uuid4())])
        MicroPage.objects.filter(pk=page['pageId']).update(draft_config=config)
        path = f"{test_micro_flow.PAGES}/{page['pageId']}/release-report"
        report = self.send(self.client, 'post', path, {'expectedRevision': 1, 'expectedPublicationRevision': 0})
        self.assertEqual(report.status_code, 200, report.content)
        result = report.json()['data']
        self.assertFalse(result['canPublish'])
        self.assertGreaterEqual(len(result['issues']), 3)
        self.assertEqual(self.send(self.client, 'post', path, {'expectedRevision': 100, 'expectedPublicationRevision': 0}).status_code, 409)

    def test_report_canonical_uuid_and_chinese_dynamic_warning(self):
        from pages.models import MicroPage
        row = self.campaign('UPPER', issued_quantity=5)
        page = self.create()
        config = config_v4([str(row.id).upper()])
        MicroPage.objects.filter(pk=page['pageId']).update(draft_config=config)
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=4):
            result = self.send(self.client, 'post', f"{test_micro_flow.PAGES}/{page['pageId']}/release-report",
                {'expectedRevision': 1, 'expectedPublicationRevision': 0}).json()['data']
        self.assertTrue(result['canPublish'], result)
        warnings = [item for item in result['issues'] if item['severity'] == 'WARNING']
        self.assertEqual(len(warnings), 1)
        self.assertIn('已领完', warnings[0]['message'])

    def test_member_auth_counts_all_states_and_hidden_scope(self):
        from benefits.page_coupons import page_coupon_cards
        from benefits.models import CouponAllocation
        from customers.models import Member, MemberSession
        from catalog.models import MemberGrade, Category, Product
        from unittest.mock import patch
        member = Member.objects.create(wechat_app_id='test', wechat_openid='member', grade=MemberGrade.objects.first())
        other = Member.objects.create(wechat_app_id='test', wechat_openid='other', grade=MemberGrade.objects.first())
        row = self.campaign('member')
        future = self.campaign('future', valid_from=timezone.now()+timedelta(days=1), valid_until=timezone.now()+timedelta(days=2))
        paused = self.campaign('paused', issuance_enabled=False)
        from benefits.coupon_operations import claim_coupon
        with patch('benefits.coupon_operations.matches_active_app', return_value=True):
            claim_coupon(member, row.id, uuid.uuid4())
        props = coupon_props([str(value.id) for value in (row, future, paused)])
        self.assertEqual([card['claimState'] for card in page_coupon_cards(props, member)], ['LIMIT_REACHED', 'NOT_STARTED', 'UNAVAILABLE'])
        self.assertEqual(page_coupon_cards(props, other)[0]['claimState'], 'AVAILABLE')
        self.assertEqual(len(page_coupon_cards(coupon_props(source='AUTO'))), 1)
        category = Category.objects.create(name='秘密类')
        hidden = Product.objects.create(name='内部草稿商品', product_no='COUPON-PRIVATE', category=category, status='DRAFT')
        scoped = self.campaign('scope', product_ids=[str(hidden.id)])
        safe = page_coupon_cards(coupon_props([str(scoped.id)]))[0]
        self.assertEqual(safe['productIds'], [])
        self.assertEqual(safe['productNames'], [])
        self.assertTrue(safe['scopeRestricted'])
        page = self.save(self.create(), config_v4([str(row.id)])).json()['data']
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=4):
            version = self.publish(page, key='member-coupon-page').json()['data']['versionId']
        token, _ = MemberSession.issue(member)
        path = f"/api/v1/app/pages/{page['pageId']}/coupons?versionId={version}"
        with patch('customers.auth.matches_active_app', return_value=True):
            result = Client().get(path, HTTP_AUTHORIZATION='Bearer ' + token)
            self.assertEqual(result.json()['data']['memberId'], str(member.id))
            self.assertEqual(result.json()['data']['componentData']['coupons'][0]['claimState'], 'LIMIT_REACHED')
            member.enabled = False
            member.save(update_fields=['enabled'])
            self.assertEqual(Client().get(path, HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)

    def test_permissions_preview_duplicates_hidden_components_and_home(self):
        from accounts.models import PermissionGroup, GroupPermission, AdminAccount
        from .test_micro_flow import PASSWORD
        group = PermissionGroup.objects.create(code='phase4-reader', name='页面只读')
        GroupPermission.objects.create(group=group, code='page.read')
        reader = AdminAccount.objects.create_user('phase4-reader', PASSWORD, display_name='页面读者')
        reader.permission_groups.add(group)
        client = self.login(reader)
        self.assertEqual(self.send(client, 'post', test_micro_flow.PAGES+'/coupon-preview', {'props': coupon_props(source='AUTO')}).status_code, 403)
        self.assertEqual(self.send(self.client, 'post', test_micro_flow.PAGES+'/coupon-preview', {'props': coupon_props(source='AUTO')}).status_code, 200)
        row = self.campaign('home')
        config = config_v4([str(row.id)])
        config['pageType'] = 'HOME'
        home = self.client.get(test_micro_flow.PAGES+'/home/draft').json()['data']
        saved = self.send(self.client, 'put', test_micro_flow.PAGES+'/home/draft', {'expectedRevision': home['revision'], 'config': config})
        home = saved.json()['data']
        self.assertEqual(self.send(client, 'post', test_micro_flow.PAGES+'/home/preview', {'expectedRevision': home['revision']}).status_code, 403)
        preview = self.send(self.client, 'post', test_micro_flow.PAGES+'/home/preview', {'expectedRevision': home['revision']}).json()['data']
        self.assertEqual(preview['componentData'], {})
        self.assertEqual(preview['couponData']['coupons'][0]['id'], str(row.id))
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=4):
            version = self.send(self.client, 'post', test_micro_flow.PAGES+'/home/publish', {
                'expectedRevision': home['revision'], 'expectedPublicationRevision': home['publicationRevision']},
                key='home-coupon-publish', confirmation=self.confirm(home)).json()['data']['versionId']
        result = Client().get('/api/v1/app/pages/home/coupons?versionId='+version)
        self.assertEqual(result.json()['data']['pageId'], 'home')
        self.assertEqual(Client().get('/api/v1/app/pages/home/coupons?versionId='+version+'&versionId='+version).status_code, 400)
        hidden = config_v4([str(row.id)])
        hidden['components'][0]['visible'] = False
        page = self.save(self.create(), hidden).json()['data']
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=4):
            version = self.publish(page, key='hidden-coupon-publish').json()['data']['versionId']
        self.assertEqual(Client().get(f"/api/v1/app/pages/{page['pageId']}/coupons?versionId={version}").json()['data']['componentData'], {})

    def test_reports_scan_all_assets_links_and_product_refs(self):
        from pages.models import MicroPage, PageConfigVersion, PagePublication
        from accounts.models import AuditLog
        page = self.create()
        ids = [str(uuid.uuid4()) for _ in range(4)]
        config = config_v4()
        config['metadata']['share']['coverAssetId'] = ids[0]
        config['components'] = [
            {'componentId': 'mosaic', 'type': 'MOSAIC', 'sortOrder': 0, 'visible': True,
             'props': {'template': 'TWO', 'gap': 8, 'items': [{'assetId': ids[0], 'link': {'type': 'PRODUCT', 'targetId': ids[2]}}, {'assetId': ids[1]}]}},
            {'componentId': 'products', 'type': 'PRODUCT_LIST', 'sortOrder': 1, 'visible': True,
             'props': {'source': 'MANUAL', 'productIds': ids[2:], 'categoryId': '', 'limit': 2, 'layout': 'GRID', 'sort': 'NEWEST'}},
            {'componentId': 'category', 'type': 'PRODUCT_LIST', 'sortOrder': 2, 'visible': True,
             'props': {'source': 'CATEGORY', 'productIds': [], 'categoryId': ids[2], 'limit': 2, 'layout': 'GRID', 'sort': 'NEWEST'}}]
        MicroPage.objects.filter(pk=page['pageId']).update(draft_config=config)
        counts = (AuditLog.objects.count(), PageConfigVersion.objects.count())
        result = self.send(self.client, 'post', f"{test_micro_flow.PAGES}/{page['pageId']}/release-report",
            {'expectedRevision': 1, 'expectedPublicationRevision': 0})
        self.assertEqual(result.status_code, 200)
        paths = [item['path'] for item in result.json()['data']['issues']]
        for expected in ['components.0.props.items.0.assetId', 'components.0.props.items.1.assetId',
                         'components.0.props.items.0.link', 'components.1.props.productIds.0',
                         'components.1.props.productIds.1', 'components.2.props.categoryId', 'metadata.share.coverAssetId']:
            self.assertIn(expected, paths)
        self.assertEqual((AuditLog.objects.count(), PageConfigVersion.objects.count()), counts)
        self.assertEqual(MicroPage.objects.get(pk=page['pageId']).draft_revision, 1)
        self.assertIsNone(PagePublication.objects.get(page_id=page['pageId']).current_version_id)

    def test_report_graph_cycle_and_endpoint_validation(self):
        first, second = self.create(), self.create()
        self.publish(second, key='report-second-publish')
        first = self.save(first, self.page_link_config(first, second['pageId'])).json()['data']
        self.publish(first, key='report-first-publish')
        second = self.save(second, self.page_link_config(second, first['pageId'])).json()['data']
        path = f"{test_micro_flow.PAGES}/{second['pageId']}/release-report"
        result = self.send(self.client, 'post', path, {'expectedRevision': second['revision'], 'expectedPublicationRevision': second['publicationRevision']})
        self.assertFalse(result.json()['data']['canPublish'])
        self.assertTrue(any('循环' in item['message'] for item in result.json()['data']['issues']))
        self.assertEqual(self.send(self.client, 'post', path, {'expectedRevision': second['revision'], 'expectedPublicationRevision': 100}).status_code, 409)
        self.assertEqual(self.send(self.client, 'post', path, {}).status_code, 400)
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.send(self.client, 'post', f"{test_micro_flow.PAGES}/{uuid.uuid4()}/release-report",
            {'expectedRevision': 1, 'expectedPublicationRevision': 0}).status_code, 404)
        self.assertEqual(self.send(self.client, 'post', test_micro_flow.PAGES+'/coupon-preview', {}).status_code, 400)
        self.assertEqual(self.client.get(test_micro_flow.PAGES+'/coupon-preview').status_code, 405)
        self.assertEqual(Client().get(f'/api/v1/app/pages/{uuid.uuid4()}/coupons?versionId={uuid.uuid4()}').status_code, 404)
        self.assertEqual(self.send(self.client, 'post', f'/api/v1/app/pages/home/coupons?versionId={uuid.uuid4()}').status_code, 405)


    def test_public_auto_coupon_selector_does_not_expose_ignored_private_ids(self):
        from pages.models import PageConfigVersion
        private = self.campaign('ignored-private', status='DRAFT')
        config = config_v4([str(private.id)])
        config['components'][0]['props']['source'] = 'AUTO'
        page = self.save(self.create(), config).json()['data']
        with override_settings(PAGE_RUNTIME_SCHEMA_VERSION=4):
            published = self.publish(page, key='ignored-private-auto').json()['data']
        result = Client().get(f"/api/v1/app/pages/{page['pageId']}?schemaVersion=4")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['data']['config']['components'][0]['props']['campaignIds'], [])
        self.assertNotIn(str(private.id), result.content.decode())
        snapshot = PageConfigVersion.objects.get(pk=published['versionId']).config_json
        self.assertEqual(snapshot['components'][0]['props']['campaignIds'], [str(private.id)])


    def test_release_report_detects_name_only_public_change(self):
        page = self.create('原页面名称')
        self.publish(page, key='report-name-initial')
        page = self.save(page, page['config'], name='新页面名称').json()['data']
        result = self.send(self.client, 'post', f"{test_micro_flow.PAGES}/{page['pageId']}/release-report", {
            'expectedRevision': page['revision'], 'expectedPublicationRevision': page['publicationRevision']}).json()['data']
        self.assertTrue(result['diff']['nameChanged'])
        self.assertFalse(result['diff']['themeChanged'])
        self.assertFalse(result['diff']['metadataChanged'])
        self.assertEqual(result['diff']['updatedComponentIds'], [])


class ReportHelpersTests(SimpleTestCase):
    def test_slots_diff_and_malformed_incomplete_draft(self):
        from types import SimpleNamespace
        from pages.release_reports import differences, link_slots, asset_slots, collect_issues
        self.assertEqual(asset_slots('IMAGE', {'assetId': 'id'}), [('assetId', 'id')])
        self.assertEqual(link_slots('CAROUSEL', {'slides': [{'link': {'type': 'FUNCTION', 'targetId': 'SEARCH'}}]}), [('slides.0.link', {'type': 'FUNCTION', 'targetId': 'SEARCH'})])
        self.assertEqual(link_slots('CAROUSEL', {'slides': False}), [])
        config = config_v4()
        original = config_v4()
        config['components'][0]['sortOrder'] = 8
        self.assertTrue(differences(config, original)['orderChanged'])
        self.assertEqual(differences(None, None)['addedComponentIds'], [])
        incomplete = {'schemaVersion': 'bad', 'components': [None, {'componentId': 'bad', 'props': None},
                      {'componentId': 'hidden', 'type': 'IMAGE', 'visible': False, 'sortOrder': 0, 'props': {}}]}
        result = collect_issues(SimpleNamespace(draft_config=incomplete, page_type='MICRO', id=uuid.uuid4()))
        self.assertTrue(any(item['code'] == 'PAGE_RUNTIME_UNSUPPORTED' for item in result))

    def test_public_product_source_projects_unused_selectors_without_mutation(self):
        from django.test import RequestFactory
        from pages.runtime import public_config
        private = str(uuid.uuid4())
        category = str(uuid.uuid4())
        config = config_v4()
        config['components'][0] = {**config['components'][0], 'type': 'PRODUCT_LIST', 'props': {
            'source': 'CATEGORY', 'productIds': [private], 'categoryId': category,
            'limit': 10, 'layout': 'GRID', 'sort': 'NEWEST'}}
        request = RequestFactory().get('/?schemaVersion=4')
        result = public_config(request, config)
        self.assertEqual(result['components'][0]['props']['productIds'], [])
        self.assertEqual(result['components'][0]['props']['categoryId'], category)
        self.assertEqual(config['components'][0]['props']['productIds'], [private])
        config['components'][0]['props']['source'] = 'MANUAL'
        result = public_config(request, config)
        self.assertEqual(result['components'][0]['props']['categoryId'], '')
        self.assertEqual(result['components'][0]['props']['productIds'], [private])
        self.assertEqual(config['components'][0]['props']['categoryId'], category)
