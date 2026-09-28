"""Dynamic identity reaches existing consumers without enabling any new writes."""
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.test import override_settings

from customers.auth import configured_credentials, resolve_member
from customers.models import MemberSession, WechatCodeUse, Member
from customers.tests.test_customer_flow import request
from payments.wechat_config import WechatGatewayError

from .test_api import APP, OTHER, SECRET, CredentialApiBase


class CredentialReaderTests(CredentialApiBase):
    def test_managed_credentials_reach_login_member_coupon_and_exchange(self):
        self.assertEqual(self.save().status_code, 200)
        from benefits.coupon_operations import _member_lock
        from points_exchange.orders import _member
        from django.db import transaction
        with override_settings(WECHAT_MINI_APP_ID=OTHER, WECHAT_MINI_APP_SECRET='old-env'):
            self.assertEqual(configured_credentials(), (APP, SECRET))
            with patch('customers.views.exchange_code', return_value='new-member') as exchange:
                result = self.client.post('/api/v1/app/auth/wechat', {'code': 'valid-fresh-code'},
                                          content_type='application/json')
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(exchange.call_args.kwargs['credentials'].app_id, APP)
            member = Member.objects.get()
            token = result.json()['data']['accessToken']
            self.assertEqual(resolve_member(request('GET', '/me', token=token)), member)
            with transaction.atomic():
                self.assertEqual(_member_lock(member), member)
                self.assertEqual(_member(member), member)
            self.key.unlink()
            self.assertIsNone(resolve_member(request('GET', '/me', token=token)))
            with transaction.atomic(), self.assertRaises(ValueError):
                _member_lock(member)
            with transaction.atomic(), self.assertRaises(ValueError):
                _member(member)

    def test_login_changed_snapshot_never_persists_misbound_member(self):
        def exchange(_code, *, credentials):
            self.assertEqual(credentials.app_id, APP)
            self.assertEqual(self.save(app_id=OTHER).status_code, 200)
            return 'openid-from-old-application'
        with patch('customers.views.exchange_code', side_effect=exchange):
            result = self.client.post('/api/v1/app/auth/wechat', {'code': 'fresh-before-change'},
                                      content_type='application/json')
        self.assertEqual(result.status_code, 503, result.content)
        self.assertFalse(Member.objects.exists())
        self.assertFalse(MemberSession.objects.exists())
        self.assertFalse(WechatCodeUse.objects.exists())

    def test_snapshot_is_immutable_and_missing_singleton_fails_closed(self):
        from wechat_integration.credentials import effective_credentials, CredentialsUnavailable
        from wechat_integration.models import MiniProgramIntegration
        snapshot = effective_credentials()
        with self.assertRaises(FrozenInstanceError):
            snapshot.secret = 'changed'
        MiniProgramIntegration.objects.all().delete()
        with self.assertRaises(CredentialsUnavailable):
            configured_credentials()
        result = self.client.post('/api/v1/app/auth/wechat', {'code': 'fresh-missing-row'},
                                  content_type='application/json')
        self.assertEqual(result.status_code, 503)

    def test_default_payment_gateway_rejects_different_effective_application(self):
        from payments.wechat_gateway import WechatGateway
        from types import SimpleNamespace
        self.assertEqual(self.save().status_code, 200)
        with patch('payments.wechat_gateway.load_wechat_config', return_value=SimpleNamespace(app_id=OTHER)):
            with self.assertRaises(WechatGatewayError) as caught:
                WechatGateway()
        self.assertEqual(caught.exception.code, 'WECHAT_APP_ID_MISMATCH')
