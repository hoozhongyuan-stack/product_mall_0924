"""Real PostgreSQL fencing for home identity and authority after lock waits."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.db import connections
from django.test import TransactionTestCase
from pages.models import HomeSelection, HomeSelectionRequest, MicroPage, PagePublication
from pages.views import _lock_publication_graph
from . import test_unification
from . import test_storefront as fixtures


class HomeSelectionConcurrencyTests(TransactionTestCase):
    login = fixtures.StorefrontTests.login
    send = fixtures.StorefrontTests.send
    page = test_unification.UnificationTests.page
    select = test_unification.UnificationTests.select

    def setUp(self):
        from accounts.models import AdminAccount
        self.owner = AdminAccount.objects.create_user('home-concurrent-owner',
            'Safe owner passphrase 2026!', kind=AdminAccount.Kind.OWNER)
        home, _ = MicroPage.objects.get_or_create(page_type='HOME')
        PagePublication.objects.get_or_create(page=home)
        HomeSelection.objects.get_or_create(pk=1, defaults={'page': home})
        self.first, self.second = self.page(), self.page()

    def test_two_home_selections_same_revision_only_one_commits(self):
        clients = [self.login(self.owner), self.login(self.owner)]
        barrier = Barrier(2, timeout=10)

        def together():
            barrier.wait()
            _lock_publication_graph()

        def choose(client, page, key):
            try:
                return self.send(client, 'post', f'/api/v1/admin/pages/{page.id}/set-home',
                    {'expectedHomeRevision': 1, 'expectedPublicationRevision': 1}, key=key)
            finally:
                connections.close_all()

        with patch('pages.views._lock_publication_graph', side_effect=together), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(choose, clients[0], self.first, 'home-concurrent-first'),
                       pool.submit(choose, clients[1], self.second, 'home-concurrent-second')]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(item.status_code for item in results), [200, 409])
        self.assertEqual(HomeSelection.objects.get(pk=1).revision, 2)
        self.assertEqual(HomeSelectionRequest.objects.count(), 1)

    def test_permission_revoked_while_waiting_for_home_lock(self):
        import time
        from threading import Event
        from django.db import connection, transaction
        from accounts.models import AdminAccount, GroupPermission, PermissionGroup
        staff = AdminAccount.objects.create_user('home-selection-staff',
            'Safe owner passphrase 2026!', kind=AdminAccount.Kind.STAFF)
        group = PermissionGroup.objects.create(code='home-selection-group', name='首页发布')
        staff.permission_groups.add(group)
        for code in ('page.read', 'page.publish'):
            GroupPermission.objects.create(group=group, code=code)
        client, started, pids = self.login(staff), Event(), []

        def choose():
            try:
                connection.ensure_connection()
                pids.append(connection.connection.info.backend_pid)
                started.set()
                return self.send(client, 'post', f'/api/v1/admin/pages/{self.first.id}/set-home',
                    {'expectedHomeRevision': 1, 'expectedPublicationRevision': 1}, key='home-revoked-lock')
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                HomeSelection.objects.select_for_update().get(pk=1)
                future = pool.submit(choose)
                self.assertTrue(started.wait(5))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', [pids[0]])
                        row = cursor.fetchone()
                    if row and row[0] == 'Lock':
                        break
                    time.sleep(.02)
                else:
                    self.fail('home selection did not wait for its row lock')
                GroupPermission.objects.filter(group=group, code='page.publish').delete()
            result = future.result(timeout=15)
        self.assertEqual(result.status_code, 403, result.content)
        self.assertEqual(HomeSelection.objects.get(pk=1).revision, 1)
        self.assertEqual(HomeSelectionRequest.objects.count(), 0)
