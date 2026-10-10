import json
import uuid
from datetime import timedelta
from django.test import TestCase
from django.db import transaction
from django.utils import timezone
from . import test_map_configuration as map_fixture


class SettlementSettingsTests(TestCase):
    setUp=map_fixture.MapConfigurationTests.setUp
    post=map_fixture.MapConfigurationTests.post

    def confirm(self, revision=1):
        response=self.post('/api/v1/admin/auth/confirm',{'action':'stores.settlement.configure','objectId':'aftersale-policy','revision':revision,'password':'Map safe password 2026'})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['data']['confirmationToken']

    def put(self,body,token=''):
        return self.client.put('/api/v1/admin/stores/settlement-settings',data=json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,HTTP_X_ACTION_CONFIRMATION=token)

    def test_policy_update_revision_existing_order_snapshot_is_preserved(self):
        from aftersales.models import AfterSalePolicy,OrderAfterSaleSnapshot
        from aftersales.service import snapshot_order_policy_locked
        from orders.models import Order
        from customers.models import Member
        from catalog.models import MemberGrade
        grade=MemberGrade.objects.first() or MemberGrade.objects.create(name='Policy test grade',code='POLICY',rank=99)
        member=Member.objects.create(member_no='SETTLEMENT-MEMBER',grade=grade,wechat_app_id='wx-policy',wechat_openid='policy-test')
        def order():
            return Order.objects.create(order_no=uuid.uuid4().hex,member=member,quote_id=uuid.uuid4(),payment_method='OFFLINE',goods_total_fen=100,shipping_fee_fen=0,payable_fen=100,expires_at=timezone.now()+timedelta(minutes=10))
        with transaction.atomic():
            previous=order()
            snapshot_order_policy_locked(previous)
        self.assertEqual(self.client.get('/api/v1/admin/stores/settlement-settings').json()['data']['receivedWindowDays'],15)
        result=self.put({'expectedRevision':1,'receivedWindowDays':30},self.confirm())
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data'],{'revision':2,'receivedWindowDays':30,'appliesTo':'NEW_ORDERS'})
        with transaction.atomic():
            current=order()
            snapshot_order_policy_locked(current)
        self.assertEqual(OrderAfterSaleSnapshot.objects.get(order=previous).received_window_days,15)
        self.assertEqual(OrderAfterSaleSnapshot.objects.get(order=current).received_window_days,30)
        self.assertEqual(self.put({'expectedRevision':1,'receivedWindowDays':20},self.confirm()).status_code,409)
        self.assertEqual(AfterSalePolicy.objects.get(pk=1).received_window_days,30)

    def test_auth_csrf_confirmation_and_invalid_values(self):
        from django.test import Client
        from accounts.models import AdminAccount
        self.assertEqual(Client().get('/api/v1/admin/stores/settlement-settings').status_code,401)
        self.assertEqual(self.client.put('/api/v1/admin/stores/settlement-settings',data='{}',content_type='application/json').status_code,403)
        self.assertEqual(self.put({'expectedRevision':1,'receivedWindowDays':20}).status_code,403)
        for days in (0,366,True,'20',None):
            self.assertEqual(self.put({'expectedRevision':1,'receivedWindowDays':days},self.confirm()).status_code,400)
        self.assertEqual(self.put({'expectedRevision':1,'receivedWindowDays':20,'unexpected':True},self.confirm()).status_code,400)
        self.assertEqual(self.put({'expectedRevision':False,'receivedWindowDays':20},self.confirm()).status_code,400)
        token=self.confirm()
        self.assertEqual(self.put({'expectedRevision':1,'receivedWindowDays':20},token).status_code,200)
        self.assertEqual(self.put({'expectedRevision':2,'receivedWindowDays':20},token).status_code,403)
        AdminAccount.objects.create_user('policy-staff','Map safe password 2026',kind='STAFF')
        self.post('/api/v1/admin/auth/login',{'loginName':'policy-staff','password':'Map safe password 2026'})
        self.assertEqual(self.client.get('/api/v1/admin/stores/settlement-settings').status_code,403)
