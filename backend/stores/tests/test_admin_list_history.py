import json
from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from accounts.models import AdminAccount
from payments.models import StoreWithdrawal, StoreWalletEvent
from stores.tests.test_finance import WithdrawalTests

class StoreAdminListHistoryTests(TestCase):
    def setUp(self):
        WithdrawalTests.setUp(self)
        AdminAccount.objects.create_user('history-owner','History safe phrase 2026',kind='OWNER')
        response=self.client.post('/api/v1/admin/auth/login',data=json.dumps({'loginName':'history-owner','password':'History safe phrase 2026'}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        StoreWithdrawal.objects.bulk_create([StoreWithdrawal(store=self.store,member=self.member,request_key=uuid4(),request_digest='0'*64,amount_fen=10,payee_name='测试人',bank_name='测试银行',encrypted_bank_account='synthetic-never-decrypt',bank_account_tail='1234',status='REJECTED',reason=f'历史说明{i:03}') for i in range(125)])
    def test_real_sql_paging_filters_and_masked_details_beyond_100_without_wallet_writes(self):
        path=f'/api/v1/admin/stores/{self.store.id}/account'
        before=self.wallet.available_fen
        response=self.client.get(path,{'withdrawalPage':6,'withdrawalPageSize':20})
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()['data']
        self.assertEqual(data['withdrawalPagination']['total'],125)
        self.assertEqual(len(data['withdrawals']),20)
        self.assertEqual(response.json()['meta']['withdrawal'],data['withdrawalPagination'])
        self.assertEqual(data['withdrawals'][0]['bankAccount'],'****1234')
        response=self.client.get(path,{'withdrawalSearch':'历史说明124','withdrawalStatus':'REJECTED'})
        data=response.json()['data']
        self.assertEqual(data['withdrawalPagination']['total'],1)
        self.assertEqual(data['withdrawals'][0]['reason'],'历史说明124')
        self.wallet.refresh_from_db();self.assertEqual(self.wallet.available_fen,before)
        self.assertEqual(StoreWalletEvent.objects.count(),0)
    def test_bad_status_duplicate_date_overflow_and_read_permission_are_server_checked(self):
        path=f'/api/v1/admin/stores/{self.store.id}/account'
        for query in ['withdrawalStatus=PAID&withdrawalStatus=REJECTED','withdrawalDateTo=9999-12-31','incomePageSize=101','incomeStatus=NOT_A_STATUS']:
            response=self.client.get(path+'?'+query)
            self.assertEqual(response.status_code,400,response.content)
        self.client.logout()
        self.assertEqual(self.client.get(path).status_code,401)
    def test_day_filter_selects_complete_history_and_excludes_previous_day(self):
        path=f'/api/v1/admin/stores/{self.store.id}/account'
        older=timezone.now()-timedelta(days=2)
        with patch('django.utils.timezone.now',return_value=older):
            StoreWithdrawal.objects.create(store=self.store,member=self.member,request_key=uuid4(),request_digest='0'*64,amount_fen=10,payee_name='测试人',bank_name='测试银行',encrypted_bank_account='synthetic-never-decrypt',bank_account_tail='1234',status='REJECTED',reason='较早的完整记录')
        day=timezone.localdate().isoformat()
        response=self.client.get(path,{'withdrawalDateFrom':day,'withdrawalDateTo':day,'withdrawalPageSize':100})
        data=response.json()['data']
        self.assertEqual(data['withdrawalPagination']['total'],125)
        self.assertEqual(len(data['withdrawals']),100)
        response=self.client.get(path,{'withdrawalDateTo':timezone.localdate(older).isoformat()})
        self.assertEqual(response.json()['data']['withdrawalPagination']['total'],1)
