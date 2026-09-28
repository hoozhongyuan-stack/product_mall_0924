"""PostgreSQL lock regression: first login and AppID changes share one lock."""
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from threading import Event
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.db import close_old_connections, connections
from django.test import Client, TransactionTestCase, override_settings

from accounts.models import AdminAccount
from catalog.models import MemberGrade
from customers.models import Member
from wechat_integration.credentials import integration_row
from .test_api import APP, BASE, OTHER, PASSWORD, SECRET


@override_settings(WECHAT_MINI_APP_ID=APP, WECHAT_MINI_APP_SECRET=SECRET)
class IntegrationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        key = Path(self.directory.name) / 'key'
        key.write_bytes(Fernet.generate_key())
        settings = override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=str(key))
        settings.enable()
        self.addCleanup(settings.disable)
        MemberGrade.objects.get_or_create(code='normal', defaults={'name': '普通会员', 'rank': 0})
        AdminAccount.objects.create_user('concurrent-owner', PASSWORD, kind='OWNER')
        self.admin = Client()
        self.admin.post('/api/v1/admin/auth/login', {'loginName': 'concurrent-owner', 'password': PASSWORD},
                        content_type='application/json')
        self.confirmation = self.admin.post('/api/v1/admin/auth/confirm', {
            'action': 'wechat.integration.update', 'objectId': 'wechat-mini-program',
            'revision': 0, 'password': PASSWORD}, content_type='application/json').json()['data']['confirmationToken']

    def threaded(self, call):
        close_old_connections()
        try:
            return call()
        finally:
            connections.close_all()

    def save(self):
        return self.admin.put(BASE, {'expectedRevision': 0, 'appId': OTHER,
            'secretAction': 'REPLACE', 'appSecret': SECRET}, content_type='application/json',
            HTTP_X_ACTION_CONFIRMATION=self.confirmation)

    def login(self):
        return Client().post('/api/v1/app/auth/wechat', {'code': 'one-time-concurrency-code'},
                             content_type='application/json')

    def test_configuration_change_while_code_exchange_waits_rejects_old_identity(self):
        exchanging, release = Event(), Event()
        def exchange(_code, *, credentials):
            exchanging.set()
            if not release.wait(5):
                raise AssertionError('test exchange was not released')
            return 'old-app-openid'
        with ThreadPoolExecutor(max_workers=1) as pool, patch('customers.views.exchange_code', side_effect=exchange):
            login = pool.submit(self.threaded, self.login)
            try:
                self.assertTrue(exchanging.wait(5))
                self.assertEqual(self.save().status_code, 200)
            finally:
                release.set()
            self.assertEqual(login.result(timeout=5).status_code, 503)
        self.assertFalse(Member.objects.exists())

    def test_first_login_holds_singleton_lock_until_member_identity_is_committed(self):
        locked, release, writer_started = Event(), Event(), Event()
        def lock(*, lock=False):
            row = integration_row(lock=lock)
            locked.set()
            if not release.wait(5):
                raise AssertionError('test login lock was not released')
            return row
        def save():
            writer_started.set()
            return self.save()
        with ThreadPoolExecutor(max_workers=2) as pool, \
                patch('customers.views.exchange_code', return_value='first-openid'), \
                patch('customers.views.integration_row', side_effect=lock):
            login = pool.submit(self.threaded, self.login)
            try:
                self.assertTrue(locked.wait(5))
                save_future = pool.submit(self.threaded, save)
                self.assertTrue(writer_started.wait(5))
                with self.assertRaises(FutureTimeout):
                    save_future.result(timeout=0.15)
            finally:
                release.set()
            self.assertEqual(login.result(timeout=5).status_code, 200)
            changed = save_future.result(timeout=5)
            self.assertEqual(changed.status_code, 409, changed.content)
            self.assertEqual(changed.json()['error']['code'], 'APP_ID_LOCKED')
        self.assertEqual(list(Member.objects.values_list('wechat_app_id', flat=True)), [APP])
