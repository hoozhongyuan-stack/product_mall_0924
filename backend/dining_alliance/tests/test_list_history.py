from django.test import SimpleTestCase
from django.http import QueryDict
from dining_alliance.access import DiningError
from dining_alliance.read_history import history_page

class AdminHistoryReadTests(SimpleTestCase):
    def test_filters_before_paging_and_reads_beyond_recent_100(self):
        rows=[{'id':str(i),'orderNo':f'ORDER{i}', 'status':'AVAILABLE' if i%2 else 'PENDING','createdAt':'2026-10-10T12:00:00+08:00'} for i in range(125)]
        result=history_page(rows, QueryDict('incomePage=3&incomePageSize=20&incomeStatus=AVAILABLE'),'income',{'AVAILABLE','PENDING'})
        self.assertEqual(result['total'],62)
        self.assertEqual(result['items'][0]['id'],'81')
        self.assertEqual(len(result['items']),20)
        result=history_page(rows,QueryDict('incomeSearch=ORDER124'),'income',{'AVAILABLE','PENDING'})
        self.assertEqual(result['items'][0]['id'],'124')
    def test_rejects_invalid_duplicate_or_unbounded_filters(self):
        for query in ['incomePage=0','incomePageSize=101','incomeStatus=UNKNOWN','incomeDateFrom=2026-02-30','incomeDateTo=9999-12-31','incomeDateFrom=2026-10-11&incomeDateTo=2026-10-10','incomeSearch=a&incomeSearch=b']:
            with self.subTest(query=query),self.assertRaises(DiningError):
                history_page([],QueryDict(query),'income',{'AVAILABLE'})
    def test_inclusive_dates_and_text_and_zero_results(self):
        rows=[{'id':'old','reason':'需要核对','status':'HELD','createdAt':'2026-10-09T23:59:59+08:00'},{'id':'current','reason':'需要核对','status':'HELD','createdAt':'2026-10-10T23:59:59+08:00'}]
        result=history_page(rows,QueryDict('withdrawalDateFrom=2026-10-10&withdrawalDateTo=2026-10-10&withdrawalSearch=核对'),'withdrawal',{'HELD'})
        self.assertEqual([r['id'] for r in result['items']],['current'])
        self.assertEqual(history_page(rows,QueryDict('withdrawalSearch=none'),'withdrawal',{'HELD'})['total'],0)

from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from dining_alliance.finance import account_data
from payments.store_accounts import account_data as store_account_data, history_queryset
from stores.access import StoreError

class HistoryBalanceIsolationTests(SimpleTestCase):
    def test_dining_history_search_never_filters_available_balance_or_frozen_amount(self):
        incomes=[{'orderNo':f'ORDER{i}','commissionFen':100,'status':'AVAILABLE','createdAt':'2026-10-10T12:00:00+08:00'} for i in range(125)]
        withdrawals=[SimpleNamespace(id=str(i),amount_fen=10,status='PENDING',reason='',revision=1,created_at=__import__('datetime').datetime.fromisoformat('2026-10-10T12:00:00+08:00')) for i in range(125)]
        with patch('dining_alliance.finance.DiningOrderSnapshot.objects') as snapshots,patch('dining_alliance.finance.Withdrawal.objects') as requests,patch('dining_alliance.finance.income_data',side_effect=lambda row:row):
            snapshots.filter.return_value.select_related.return_value.order_by.return_value=incomes
            requests.filter.return_value.order_by.return_value=withdrawals
            result=account_data(object(),QueryDict('incomeSearch=ORDER124&withdrawalPage=6&withdrawalPageSize=20'))
            self.assertEqual(result['availableFen'],11250)
            self.assertEqual(result['frozenFen'],1250)
            self.assertEqual(result['incomes'][0]['orderNo'],'ORDER124')
            self.assertEqual(result['incomePagination']['total'],1)
            self.assertEqual(len(result['withdrawals']),20)
            self.assertEqual(result['withdrawals'][0]['id'],'100')
            legacy=account_data(object())
            self.assertEqual((len(legacy['incomes']),len(legacy['withdrawals'])),(100,100))
    def test_store_query_paginates_before_serialization_and_preserves_wallet_summary(self):
        income=MagicMock();withdrawals=MagicMock()
        income.order_by.return_value=income;withdrawals.order_by.return_value=withdrawals
        income.count.return_value=125;withdrawals.count.return_value=125
        income.__getitem__.side_effect=lambda span:list(range(125))[span]
        withdrawals.__getitem__.side_effect=lambda span:list(range(125))[span]
        summary={'balance':{'availableFen':8200,'frozenFen':1000},'income':[],'withdrawals':[]}
        with patch('payments.store_accounts.account_summaries',return_value=[summary]),patch('payments.store_accounts.history_queryset',side_effect=[income,withdrawals]),patch('payments.store_accounts.income_data',side_effect=lambda row:{'id':row}),patch('payments.store_accounts.withdrawal_data',side_effect=lambda row:{'id':row}):
            result=store_account_data(__import__('uuid').uuid4(),history_params=QueryDict('incomePage=6&withdrawalPage=3&withdrawalPageSize=50'))
            self.assertEqual(result['balance'],summary['balance'])
            self.assertEqual(result['income'][0]['id'],100)
            self.assertEqual(result['withdrawals'][0]['id'],100)
            self.assertEqual(result['incomePagination']['total'],125)
            self.assertEqual(result['withdrawalPagination']['total'],125)
    def test_store_history_rejects_invalid_date_boundary_before_database_query(self):
        rows=MagicMock()
        with self.assertRaises(StoreError):
            history_queryset(rows,QueryDict('withdrawalDateTo=9999-12-31'),'withdrawal',{'PAID'},'created_at',['reason'])
        rows.filter.assert_not_called()
