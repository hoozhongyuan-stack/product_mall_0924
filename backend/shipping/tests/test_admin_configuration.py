import json
from uuid import uuid4
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase,Client
from accounts.models import AdminAccount,PermissionGroup,GroupPermission,AuditLog
from shipping.models import ShippingPolicy
from shipping.configuration import zone
from shipping.rules import ShippingRuleError
from inventory.models import Warehouse
from stores.models import Store


class AdminConfigurationTests(TestCase):
    def setUp(self):
        self.owner=AdminAccount.objects.create_user('shipping-config-owner','Test-only passphrase 2026!',kind='OWNER')
        self.client=Client(enforce_csrf_checks=True)
        self.client.get('/api/v1/admin/auth/csrf')
        self.client.post('/api/v1/admin/auth/login',data=json.dumps(dict(loginName=self.owner.login_name,password='Test-only passphrase 2026!')),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.policy=ShippingPolicy.objects.get(pk=1)
        self.template=dict(name='分计费',mode='PIECE',firstFeeFen=29,firstUnits=1,extraFeeFen=2,extraUnits=2,freeThresholdFen=19900,regions=[],blockedRegions=[],productIds=[],enabled=False)

    def write(self,path,body,method='post'):
        return getattr(self.client,method)('/api/v1/admin/shipping/'+path,data=json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)

    def test_save_reload_revision_conflict_and_audit(self):
        saved=self.write('templates',dict(self.template,expectedRevision=self.policy.revision))
        self.assertEqual(saved.status_code,200,saved.content)
        self.assertEqual(saved.json()['data']['templates'][-1]['firstFeeFen'],29)
        self.assertEqual(self.client.get('/api/v1/admin/shipping/config').json()['data'],saved.json()['data'])
        stale=self.write('templates',dict(self.template,expectedRevision=self.policy.revision))
        self.assertEqual(stale.status_code,409)
        self.assertTrue(AuditLog.objects.filter(action_code='shipping.templates.post').exists())

    def test_region_code_name_cannot_be_forged(self):
        cache.set('shipping-verified-region:440000',dict(code='440000',name='广东省',level='province'))
        bad=self.write('templates',dict(self.template,expectedRevision=self.policy.revision,regions=[dict(code='440000',name='新疆维吾尔自治区',level='province')]))
        self.assertEqual(bad.status_code,400)
        good=self.write('templates',dict(self.template,expectedRevision=self.policy.revision,regions=[dict(code='440000',name='广东省',level='province')]))
        self.assertEqual(good.status_code,200,good.content)

    def test_bulk_conflict_default_templates_are_not_ambiguous(self):
        saved=self.write('templates',dict(self.template,expectedRevision=self.policy.revision))
        data=saved.json()['data'];identifier=data['templates'][-1]['id']
        conflict=self.write('bulk',dict(expectedRevision=data['revision'],resource='templates',ids=[identifier],enabled=True))
        self.assertEqual(conflict.status_code,400)
        disabled=self.write('bulk',dict(expectedRevision=data['revision'],resource='templates',ids=[data['templates'][0]['id']],enabled=False))
        self.assertEqual(disabled.status_code,200,disabled.content)
        enabled=self.write('bulk',dict(expectedRevision=disabled.json()['data']['revision'],resource='templates',ids=[identifier],enabled=True))
        self.assertEqual(enabled.status_code,200,enabled.content)

    def test_csrf_and_live_permissions_required(self):
        self.assertEqual(self.client.post('/api/v1/admin/shipping/templates',data=json.dumps(self.template),content_type='application/json').status_code,403)
        self.owner.kind='STAFF';self.owner.save()
        self.assertEqual(self.client.get('/api/v1/admin/shipping/config').status_code,403)

    def test_self_intersecting_polygon_rejected(self):
        store=Store.objects.create(name='test',warehouse=Warehouse.objects.create(code='geometry',name='仓'))
        body=dict(storeId=str(store.pk),name='围栏',kind='POLYGON',center=None,radiusMeters=None,polygon=[dict(latitude=0,longitude=0),dict(latitude=2,longitude=2),dict(latitude=0,longitude=2),dict(latitude=2,longitude=0)],priority=10,feeMode='FIXED',feeFen=600,firstMeters=1000,extraMeters=1000,extraFeeFen=200,minimumFen=0,freeThresholdFen=None,startTime='09:00',endTime='23:00',enabled=True)
        with self.assertRaises(ShippingRuleError):zone(body)


from django.test import TransactionTestCase
class ConfigurationConcurrencyTests(TransactionTestCase):
    def test_two_writers_same_revision_one_commits(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        owner=AdminAccount.objects.create_user('shipping-concurrent','Test-only passphrase 2026!',kind='OWNER')
        policy,_=ShippingPolicy.objects.get_or_create(pk=1)
        revision=policy.revision
        clients=[]
        for index in range(2):
            client=Client();client.force_login(owner)
            import time
            session=client.session;session['admin_last_active']=time.time();session['admin_auth_version']=owner.auth_version;session.save()
            clients.append(client)
        barrier=Barrier(2)
        def write(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                body=dict(expectedRevision=revision,name='并发模板'+str(index),mode='FIXED',firstFeeFen=100,firstUnits=1,extraFeeFen=0,extraUnits=1,freeThresholdFen=None,regions=[],blockedRegions=[],productIds=[],enabled=False)
                return clients[index].post('/api/v1/admin/shipping/templates',data=json.dumps(body),content_type='application/json').status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(write,[0,1]))
        self.assertEqual(sorted(results),[200,409])
        policy.refresh_from_db();self.assertEqual(policy.revision,revision+1)


class EndToEndConfigurationTests(AdminConfigurationTests):
    def catalog_scene(self):
        from orders.tests.test_order_flow import OrderFlowTests
        fixture=OrderFlowTests()
        with patch("accounts.models.AdminAccount.objects.create_user",return_value=self.owner):
            fixture.setUp()
        self.fixture=fixture
        product=fixture.product;product.fulfillment_kind='SHIP';product.save()
        store=Store.objects.create(warehouse=fixture.warehouse,name='运费仓',supported_modes=['DELIVERY','EXPRESS'],latitude=28.2,longitude=112.9)
        return fixture,store

    def test_weights_targets_store_selection_and_authoritative_trial(self):
        fixture,store=self.catalog_scene()
        target=self.client.get('/api/v1/admin/shipping/targets',{'search':'ORD-A'}).json()['data']['items'][0]['skus'][0]
        saved=self.write('weights',dict(expectedRevision=self.policy.revision,items=[dict(skuId=target['skuId'],weightGrams=1150,expectedUnitVersionId=target['unitVersionId'])]),'put')
        self.assertEqual(saved.status_code,200,saved.content)
        config=saved.json()['data'];rule=config['templates'][0]
        template={key:value for key,value in rule.items() if key!='id'}
        template.update(mode='WEIGHT',firstFeeFen=800,firstUnits=1000,extraFeeFen=200,extraUnits=1000)
        edited=self.write('templates/'+rule['id'],dict(template,expectedRevision=config['revision']),'put')
        self.assertEqual(edited.status_code,200,edited.content)
        trial=self.write('trial',dict(storeId=str(store.pk),deliveryMode='EXPRESS',goodsFen=2000,couponDiscountFen=0,items=[dict(skuId=target['skuId'],quantity=2)],address=dict(province='湖南省',city='长沙市',district='岳麓区')))
        self.assertEqual(trial.status_code,200,trial.content)
        self.assertEqual(trial.json()['data']['costFen'],1200)
        read=self.client.get('/api/v1/admin/shipping/targets',{'search':'ORD-A'}).json()['data']['items'][0]['skus'][0]
        self.assertTrue(read['weightConfigured'])
        self.assertEqual(self.client.get('/api/v1/admin/shipping/stores',{'search':'运费仓'}).json()['data']['items'][0]['id'],str(store.pk))
        self.assertEqual(self.client.get('/api/v1/admin/shipping/stores/'+str(store.pk)).json()['data']['latitude'],28.2)
        self.assertEqual(self.client.get('/api/v1/admin/shipping/stores/'+str(uuid4())).status_code,404)

    def test_zone_campaign_save_reload_delete_and_trial(self):
        fixture,store=self.catalog_scene()
        body=dict(storeId=str(store.pk),name='配送圈',kind='CIRCLE',center=dict(latitude=28.2,longitude=112.9),radiusMeters=3000,polygon=[],priority=10,feeMode='DISTANCE',feeFen=600,firstMeters=1000,extraMeters=1000,extraFeeFen=200,minimumFen=3900,freeThresholdFen=9900,startTime='00:00',endTime='23:59',enabled=True)
        saved=self.write('zones',dict(body,expectedRevision=self.policy.revision))
        self.assertEqual(saved.status_code,200,saved.content)
        config=saved.json()['data'];identifier=config['zones'][-1]['id']
        now=__import__('django.utils.timezone',fromlist=['now']).now()
        from datetime import timedelta
        campaign=dict(enabled=True,name='平台活动',startsAt=(now-timedelta(hours=1)).isoformat(),endsAt=(now+timedelta(hours=1)).isoformat(),minimumFen=0,storeIds=[str(store.pk)],deliveryModes=['DELIVERY'])
        config=self.write('config',dict(expectedRevision=config['revision'],campaign=campaign),'patch').json()['data']
        trial=self.write('trial',dict(storeId=str(store.pk),deliveryMode='DELIVERY',goodsFen=6800,couponDiscountFen=0,items=[dict(skuId=str(fixture.skus[0].pk),quantity=2)],address=dict(province='湖南省',city='长沙市',district='岳麓区',latitude=28.2,longitude=112.91)))
        self.assertEqual(trial.status_code,200,trial.content)
        self.assertEqual(trial.json()['data']['platformBearsFen'],600)
        removed=self.write('zones/'+identifier,dict(expectedRevision=config['revision']),'delete')
        self.assertEqual(removed.status_code,200,removed.content)
        self.assertEqual(removed.json()['data']['zones'],[])

    def test_invalid_campaign_and_stale_weights_do_not_change_configuration(self):
        fixture,store=self.catalog_scene()
        bad=self.write('weights',dict(expectedRevision=self.policy.revision,items=[dict(skuId=str(fixture.skus[0].pk),weightGrams=0,expectedUnitVersionId=str(fixture.skus[0].current_unit_id))]),'put')
        self.assertEqual(bad.status_code,400)
        bad=self.write('config',dict(expectedRevision=self.policy.revision,campaign=dict(enabled=True,name='bad',startsAt='2026-10-12T10:00:00',endsAt='2026-10-12T11:00:00',minimumFen=0,storeIds=[],deliveryModes=['DELIVERY'])),'patch')
        self.assertEqual(bad.status_code,400)
        self.policy.refresh_from_db();self.assertEqual(self.policy.revision,1)
