import json
from uuid import uuid4
from django.test import TestCase
from accounts.models import AdminAccount
from dining_alliance.models import Restaurant, Withdrawal
from dining_alliance.finance import change_withdrawal
from stores.tests.test_finance import WithdrawalTests

class DiningAdminHistoryApiTests(TestCase):
    def setUp(self):
        WithdrawalTests.setUp(self)
        self.restaurant=Restaurant.objects.create(name='分页餐饮店',address='测试街',contact_name='测试人',contact_phone='13800000000',latitude=22.5,longitude=114.1,assigned_store=self.store)
        AdminAccount.objects.create_user('dining-history-owner','History safe phrase 2026',kind='OWNER')
        self.client.post('/api/v1/admin/auth/login',data=json.dumps({'loginName':'dining-history-owner','password':'History safe phrase 2026'}),content_type='application/json')
        Withdrawal.objects.bulk_create([Withdrawal(restaurant=self.restaurant,member=self.member,request_key=uuid4(),amount_fen=10,status='PENDING') for i in range(125)])
        for i,row in enumerate(Withdrawal.objects.filter(restaurant=self.restaurant).order_by('id')):change_withdrawal(self.restaurant.id,row.id,'reject',{'revision':1,'reason':f'说明{i:03}'})
    def test_history_total_not_100_and_filtered_read_keeps_full_balance(self):
        path=f'/api/v1/admin/dining-alliance/restaurants/{self.restaurant.id}/account'
        response=self.client.get(path,{'withdrawalPage':6,'withdrawalPageSize':20})
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()['data']
        self.assertEqual(data['withdrawalPagination']['total'],125)
        self.assertEqual(len(data['withdrawals']),20)
        self.assertEqual(response.json()['meta']['withdrawal'],data['withdrawalPagination'])
        response=self.client.get(path,{'withdrawalSearch':'说明124','withdrawalStatus':'REJECTED'})
        filtered=response.json()['data']
        self.assertEqual(filtered['withdrawalPagination']['total'],1)
        self.assertEqual(filtered['withdrawals'][0]['reason'],'说明124')
        self.assertEqual(filtered['availableFen'],data['availableFen'])
        self.assertEqual(filtered['frozenFen'],data['frozenFen'])
    def test_invalid_status_and_overflow_dates_are_400(self):
        path=f'/api/v1/admin/dining-alliance/restaurants/{self.restaurant.id}/account'
        for query in ['withdrawalDateTo=9999-12-31','incomePage=0','withdrawalStatus=NOT_A_STATUS']:
            response=self.client.get(path+'?'+query)
            self.assertEqual(response.status_code,400,response.content)
