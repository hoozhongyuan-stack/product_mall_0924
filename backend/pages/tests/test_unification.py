"""Published home pointer and opt-in navigation contracts preserve old clients."""
import uuid
from django.test import Client, TestCase
from accounts.models import AuditLog, AdminAccount
from pages.models import MicroPage, PagePublication, PageConfigVersion
from . import test_storefront as fixtures


class UnificationTests(TestCase):
    setUp = fixtures.StorefrontTests.setUp
    login = fixtures.StorefrontTests.login
    send = fixtures.StorefrontTests.send
    confirm = fixtures.StorefrontTests.confirm
    put = fixtures.StorefrontTests.put
    publish = fixtures.StorefrontTests.publish
    asset = fixtures.StorefrontTests.asset

    def page(self, published=True):
        page = MicroPage.objects.create(page_type='MICRO', name='专题', draft_config={
            'schemaVersion': 1, 'pageType': 'MICRO',
            'theme': {'pageBackgroundColor': '#FFFFFF', 'headerBackgroundColor': '#FFFFFF',
                      'brandTextColor': '#25221F'}, 'components': []})
        publication = PagePublication.objects.create(page=page)
        if published:
            version = PageConfigVersion.objects.create(page=page, revision=1, name=page.name,
                config_json=page.draft_config, published_by=self.owner)
            publication.current_version = version
            publication.revision = 1
            publication.save()
        return page

    def nav(self, page):
        return {'schemaVersion': 2, 'items': [
            {'key': 'home', 'label': '首页', 'target': {'type': 'HOME'},
             'iconAssetId': None, 'selectedIconAssetId': None},
            {'key': 'special', 'label': '精选', 'target': {'type': 'MICRO', 'pageId': str(page.id)},
             'iconAssetId': None, 'selectedIconAssetId': None}]}

    def select(self, page, revision=1, publication=1, key='select-home-one'):
        return self.send(self.client, 'post', f'/api/v1/admin/pages/{page.id}/set-home',
            {'expectedHomeRevision': revision, 'expectedPublicationRevision': publication}, key=key)

    def test_home_pointer_preserves_old_page_and_published_snapshot(self):
        old = MicroPage.objects.get(page_type='HOME')
        old_config = old.draft_config.copy()
        page = self.page()
        selected = self.select(page)
        self.assertEqual(selected.status_code, 200, selected.content)
        self.assertEqual(selected.json()['data']['pageId'], str(page.id))
        self.assertEqual(selected.json()['data']['revision'], 2)
        public = Client().get('/api/v1/app/home').json()['data']
        self.assertEqual(public['pageId'], str(page.id))
        self.assertEqual(public['versionId'], str(page.pagepublication.current_version_id))
        page.draft_config = {**page.draft_config, 'components': [{'draftOnly': True}]}
        page.save()
        self.assertEqual(Client().get('/api/v1/app/home').json()['data']['config']['components'], [])
        self.assertEqual(self.client.get('/api/v1/admin/pages/home/draft').json()['data']['pageId'], str(page.id))
        self.assertEqual(self.client.get('/api/v1/admin/pages/home/versions').json()['data']['list'][0]['versionId'], public['versionId'])
        old.refresh_from_db()
        self.assertEqual(old.draft_config, old_config)
        rows = self.client.get('/api/v1/admin/pages?includeHome=1').json()['data']['rows']
        self.assertEqual(sum(row['isHome'] for row in rows), 1)
        self.assertIn(str(old.id), [row['pageId'] for row in rows])
        self.assertEqual(self.client.get(f'/api/v1/admin/pages/{old.id}/draft').status_code, 200)

    def test_home_replay_conflict_csrf_permissions_and_draft_rejection(self):
        page, other = self.page(), self.page()
        rejected = self.select(self.page(False), publication=0)
        self.assertEqual(rejected.status_code, 422, rejected.content)
        first = self.select(page)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(self.select(page).json()['data'], first.json()['data'])
        self.assertEqual(self.select(other).status_code, 409)
        self.assertEqual(self.select(other, key='stale-home-selection').status_code, 409)
        self.assertEqual(self.select(other, revision=2, publication=0, key='stale-target-selection').status_code, 409)
        self.assertEqual(AuditLog.objects.filter(action_code='page.set_home').count(), 1)
        csrf = self.client.post(f'/api/v1/admin/pages/{page.id}/set-home', data='{}', content_type='application/json')
        self.assertEqual(csrf.status_code, 403)
        self.assertEqual(Client().get('/api/v1/admin/pages/home-selection').status_code, 401)
        staff = AdminAccount.objects.create_user('unification-staff', 'Safe owner passphrase 2026!', display_name='只读')
        self.assertEqual(self.send(self.login(staff), 'post', f'/api/v1/admin/pages/{other.id}/set-home',
            {'expectedHomeRevision': 2, 'expectedPublicationRevision': 1}, key='no-home-permission').status_code, 403)

    def test_navigation_draft_publish_old_client_fallback_and_dual_icons(self):
        page = self.page(False)
        config = self.nav(page)
        icon, selected_icon = self.asset(), self.asset()
        config['items'][0] = {**config['items'][0], 'iconAssetId': str(icon.id), 'selectedIconAssetId': str(selected_icon.id)}
        self.assertEqual(self.put('navigation', config, 1).status_code, 200)
        failed = self.publish('navigation', 2, 0, 'nav-draft-rejected')
        self.assertEqual(failed.status_code, 422, failed.content)
        self.assertIsNone(Client().get('/api/v1/app/storefront?navigationVersion=2').json()['data']['navigation']['versionId'])
        publication = page.pagepublication
        version = PageConfigVersion.objects.create(page=page, revision=1, name=page.name,
            config_json=page.draft_config, published_by=self.owner)
        publication.current_version, publication.revision = version, 1
        publication.save()
        success = self.publish('navigation', 2, 0, 'nav-now-published')
        self.assertEqual(success.status_code, 200, success.content)
        nav = Client().get('/api/v1/app/storefront?navigationVersion=2').json()['data']['navigation']
        self.assertEqual(nav['schemaVersion'], 2)
        self.assertEqual(nav['items'][1]['target'], {'type': 'MICRO', 'pageId': str(page.id)})
        self.assertEqual(nav['items'][0]['iconUrl'], f'/api/v1/app/assets/{icon.id}/file')
        legacy = Client().get('/api/v1/app/storefront').json()['data']['navigation']
        self.assertEqual([item['key'] for item in legacy['items']], ['HOME', 'CATEGORY', 'CART', 'ME'])
        self.assertEqual(legacy['items'][0]['selectedIconUrl'], f'/api/v1/app/assets/{selected_icon.id}/file')
        self.assertEqual(Client().get('/api/v1/app/storefront?navigationVersion=99').status_code, 400)

    def test_navigation_target_controls_and_five_item_limit(self):
        config = self.nav(self.page())
        invalids = [
            {**config, 'items': []},
            {**config, 'items': config['items'] * 3},
            {**config, 'items': [config['items'][0], config['items'][0]]},
            {**config, 'items': [{**config['items'][0], 'target': {'type': 'URL', 'url': 'https://example.test'}}]},
            {**config, 'items': [{**config['items'][0], 'target': {'type': 'MICRO', 'pageId': str(uuid.uuid4())}}]},
            {**config, 'items': [{**config['items'][0], 'label': '<script>\n'}]},
        ]
        for invalid in invalids:
            self.assertIn(self.put('navigation', invalid, 1).status_code, [400, 422])
        valid = {**config, 'items': [
            {**config['items'][0], 'key': f'item-{n}', 'target': {'type': target}}
            for n, target in enumerate(['HOME', 'CATEGORY', 'CART', 'ME'])] + [config['items'][1]]}
        self.assertEqual(self.put('navigation', valid, 1).status_code, 200)

    def test_navigation_rollback_revalidates_page_and_preserves_pointer_and_draft(self):
        page, asset = self.page(False), self.asset()
        config_json = {**page.draft_config, 'components': [
            {'componentId': 'hero', 'type': 'CAROUSEL', 'sortOrder': 0, 'visible': True,
             'props': {'slides': [{'assetId': str(asset.id)}]}}]}
        version = PageConfigVersion.objects.create(page=page, revision=1, name=page.name,
            config_json=config_json, published_by=self.owner)
        publication = page.pagepublication
        publication.current_version, publication.revision = version, 1
        publication.save()
        config = self.nav(page)
        self.assertEqual(self.put('navigation', config, 1).status_code, 200)
        first = self.publish('navigation', 2, 0, 'v2-history-first')
        self.assertEqual(first.status_code, 200, first.content)
        from pages.storefront_validation import defaults
        self.assertEqual(self.put('navigation', defaults('navigation'), 2).status_code, 200)
        second = self.publish('navigation', 3, 1, 'v1-history-second')
        self.assertEqual(second.status_code, 200, second.content)
        (self.media / asset.stored_name).unlink()
        result = fixtures.StorefrontTests.rollback(self, 'navigation', 3, 2,
            first.json()['data']['versionId'], 'invalid-v2-page-rollback')
        self.assertIn(result.status_code, (400, 422), result.content)
        draft = self.client.get('/api/v1/admin/navigation/draft').json()['data']
        self.assertEqual(draft['publishedVersionId'], second.json()['data']['versionId'])
        self.assertEqual(draft['revision'], 3)
        self.assertEqual(draft['config'], defaults('navigation'))

    def test_seed_home_pointer_database_protection_and_coupon_alias(self):
        from pages.models import HomeSelection
        from django.db import IntegrityError, transaction
        from django.db.models.deletion import ProtectedError
        old = MicroPage.objects.get(page_type='HOME')
        pointer = HomeSelection.objects.get(pk=1)
        self.assertEqual(pointer.page_id, old.id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSelection.objects.create(pk=2, page=old)
        with self.assertRaises(ProtectedError):
            old.delete()
        page = self.page()
        self.assertEqual(self.select(page).status_code, 200)
        version = page.pagepublication.current_version_id
        coupons = Client().get(f'/api/v1/app/pages/home/coupons?versionId={version}')
        self.assertEqual(coupons.status_code, 200, coupons.content)
        self.assertEqual(coupons.json()['data']['componentData'], {})
        self.assertEqual(self.client.get(f'/api/v1/admin/pages/{old.id}/versions').status_code, 200)
        from pages.models import HomeSelectionRequest
        receipt = HomeSelectionRequest.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSelection.objects.filter(pk=1).update(revision=100)
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSelection.objects.filter(pk=1).delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomeSelectionRequest.objects.filter(pk=receipt.pk).update(result={})

    def test_home_selection_rejects_unavailable_published_media(self):
        page = self.page(False)
        asset = self.asset()
        publication = page.pagepublication
        config = {**page.draft_config, 'components': [
            {'componentId': 'image', 'type': 'CAROUSEL', 'sortOrder': 0, 'visible': True,
             'props': {'slides': [{'assetId': str(asset.id)}]}}]}
        version = PageConfigVersion.objects.create(page=page, revision=1, name=page.name,
            config_json=config, published_by=self.owner)
        publication.current_version, publication.revision = version, 1
        publication.save()
        (self.media / asset.stored_name).unlink()
        result = self.select(page)
        self.assertEqual(result.status_code, 422, result.content)
        self.assertEqual(self.client.get('/api/v1/admin/pages/home-selection').json()['data']['revision'], 1)
