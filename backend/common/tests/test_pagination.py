"""HTTP compatibility checks across every pagination family."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AdminAccount
from benefits.models import CouponCampaign
from catalog.models import Asset, MemberGrade
from pages.models import MicroPage, PagePublication
from customers.models import Member, MemberSession


@override_settings(WECHAT_MINI_APP_ID='wx-pagination-test')
class PaginationContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = AdminAccount.objects.create_user('http-owner', 'Test password 2026!',
                                                    display_name='测试', kind='OWNER')
        cls.asset = Asset.objects.create(kind='IMAGE', content_type='image/png', byte_size=1,
            width=1, height=1, sha256='1'*64, original_name='test.png', stored_name='product/test.png',
            created_by=cls.owner)
        grade = MemberGrade.objects.create(code='http-grade', name='测试', rank=99)
        cls.member = Member.objects.create(wechat_app_id='wx-pagination-test', wechat_openid='member', grade=grade)
        cls.token, _ = MemberSession.issue(cls.member)
        cls.campaign = CouponCampaign.objects.create(code='http-coupon', title='测试活动', kind='CASH',
            discount_fen=100, valid_from=timezone.now(), valid_until=timezone.now()+timedelta(days=1),
            status='DRAFT', total_quantity=10)
        cls.micro = MicroPage.objects.create(page_type='MICRO', name='测试微页面')
        PagePublication.objects.create(page=cls.micro)

    def setUp(self):
        result = self.client.post('/api/v1/admin/auth/login',
            {'loginName': 'http-owner', 'password': 'Test password 2026!'}, content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)

    def assert_offset(self, path, *, nested=False, member=False, private=False):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'} if member else {}
        result = self.client.get('/api/v1/' + path, {'page': 1, 'pageSize': 5}, **headers)
        self.assertEqual(result.status_code, 200, result.content)
        payload = result.json()
        legacy = payload['data']['pagination'] if nested else payload['data']
        self.assertEqual(payload.get('meta'), {key: legacy[key] for key in ('page', 'pageSize', 'total')})
        self.assertTrue(any(key in payload['data'] for key in ('items', 'rows', 'list')))
        if private:
            self.assertIn('no-store', result.get('Cache-Control', ''))

    def test_all_page_number_families_keep_legacy_fields(self):
        flat = ['admin/sku-rows', 'app/products', 'admin/orders', 'app/orders', 'admin/pages',
                'admin/assets', f'admin/assets/{self.asset.id}/references',
                'admin/aftersales', 'app/aftersales', 'admin/pages/home/versions',
                'admin/startup/versions', f'admin/pages/{self.micro.id}/versions', 'admin/navigation/versions', 'admin/customer-service/versions']
        flat += ['admin/inventory/' + path for path in ('skus', 'balances', 'inbounds', 'outbounds', 'stocktakes', 'ledgers')]
        for path in flat:
            with self.subTest(path=path):
                self.assert_offset(path, member=path.startswith('app/'),
                                   private='versions' in path or 'assets' in path or 'aftersales' in path)
        nested = ['admin/members', f'admin/members/{self.member.id}/points',
                  f'admin/members/{self.member.id}/consumption', 'app/member/points', 'app/member/consumption',
                  'admin/coupon-campaigns', f'admin/coupon-campaigns/{self.campaign.id}/issuances', 'admin/coupon-product-options', 'app/coupon-campaigns', 'app/member/coupons',
                  'admin/exchange-offers', 'admin/exchange-sku-options', 'app/exchange-products']
        for path in nested:
            with self.subTest(path=path):
                self.assert_offset(path, nested=True, member=path.startswith('app/'), private=True)

    def test_all_cursor_families_keep_private_cache_and_cursor(self):
        today = timezone.localdate().isoformat()
        for path in ('code-versions', 'code-sync-jobs', 'subscription-message-tasks', 'exports', 'audit-logs'):
            with self.subTest(path=path):
                params = {'from': today, 'to': today} if path == 'audit-logs' else {}
                result = self.client.get('/api/v1/admin/' + path, params)
                self.assertEqual(result.status_code, 200, result.content)
                payload = result.json()
                self.assertEqual(payload.get('meta'), {'nextCursor': payload['data']['nextCursor']})
                self.assertIn('no-store', result.get('Cache-Control', ''))

    def test_non_paginated_and_denied_endpoints_do_not_get_meta(self):
        result = self.client.get('/api/v1/app/categories')
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('meta', result.json())
        self.client.logout()
        for path in ('code-versions', 'members', 'coupon-campaigns', 'exchange-offers'):
            result = self.client.get('/api/v1/admin/' + path)
            self.assertEqual(result.status_code, 401, result.content)
            self.assertNotIn('meta', result.json())
            self.assertIn('no-store', result.get('Cache-Control', ''))

    def test_private_method_errors_keep_authorization_cache_policy(self):
        for path in ('code-versions', 'coupon-campaigns', 'exchange-offers'):
            with self.subTest(path=path):
                result = self.client.delete('/api/v1/admin/' + path)
                self.assertEqual(result.status_code, 405)
                self.assertIn('GET', result['Allow'])
                self.assertEqual(result.json()['error']['code'], 'METHOD_NOT_ALLOWED')
                self.assertTrue(result.json()['requestId'])
                self.assertIn('no-store', result.get('Cache-Control', ''))
