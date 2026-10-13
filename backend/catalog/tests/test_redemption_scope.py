import json
from uuid import uuid4
from django.db import IntegrityError, transaction
from django.test import TestCase
from catalog.redemption_scope import parse_scope, scope_data, scope_snapshot
from catalog.validation import CatalogError
from inventory.models import Warehouse
from stores.models import Store
from . import test_catalog_flow as fixture


class RedemptionScopeTests(TestCase):
    setUp = fixture.CatalogFlowTests.setUp
    send = fixture.CatalogFlowTests.send
    authenticated_client = fixture.CatalogFlowTests.authenticated_client
    staff_client = fixture.CatalogFlowTests.staff_client
    create_category = fixture.CatalogFlowTests.create_category
    leaf = fixture.CatalogFlowTests.leaf
    product_payload = fixture.CatalogFlowTests.product_payload

    def store(self):
        warehouse=Warehouse.objects.create(code=uuid4().hex,name='核销库存仓')
        return Store.objects.create(warehouse=warehouse,name='指定核销前置仓',supported_modes=['PICKUP'])

    def test_scope_options_use_catalog_permission_without_store_permission(self):
        store=self.store()
        _,client=self.staff_client('catalog_reader',['catalog.read'])
        result=client.get('/api/v1/admin/products/redemption-stores')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['data']['items'],[{'storeId':str(store.id),'name':store.name}])
        self.assertEqual(self.client.get('/api/v1/admin/products/redemption-stores').status_code,401)

    def test_create_patch_scope_is_revisioned_and_snapshot_data_uses_ids_names(self):
        store=self.store()
        payload=self.product_payload(self.leaf()['id'])
        payload.update(fulfillmentKind='REDEEM',redeemScope='BOTH',redeemStoreIds=[str(store.id)])
        created=self.send(self.owner,'post','/api/v1/admin/products',payload)
        self.assertEqual(created.status_code,201,created.content)
        data=self.owner.get('/api/v1/admin/products/'+created.json()['data']['productId']).json()['data']
        self.assertEqual(data['redeemStores'],[{'storeId':str(store.id),'name':store.name}])
        result=self.send(self.owner,'patch','/api/v1/admin/products/'+data['productId'],
            {'expectedRevision':data['productRevision'],'redeemScope':'STORE','redeemStoreIds':[str(store.id)]})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['redeemScope'],'STORE')
        stale=self.send(self.owner,'patch','/api/v1/admin/products/'+data['productId'],
            {'expectedRevision':data['productRevision'],'redeemScope':'PLATFORM','redeemStoreIds':[]})
        self.assertEqual(stale.status_code,409)

    def test_reject_invalid_scope_store_ids_and_physical_scope(self):
        store=self.store()
        invalid=[('REDEEM','BAD',[]),('REDEEM','STORE',[]),('REDEEM','BOTH',[]),
            ('REDEEM','PLATFORM',[str(store.id)]),('SHIP','STORE',[str(store.id)]),
            ('REDEEM','STORE',[str(uuid4())]),('REDEEM','STORE',['bad']),
            ('REDEEM','STORE',[1]),('REDEEM','STORE',[str(store.id)]*2),
            ('REDEEM','STORE',{}),('REDEEM','STORE',['bad']*101)]
        for args in invalid:
            with self.subTest(args=args),self.assertRaises(CatalogError):
                parse_scope(*args)
        store.enabled=False
        store.save(update_fields=['enabled'])
        with self.assertRaises(CatalogError):
            parse_scope('REDEEM','STORE',[str(store.id)])

    def test_default_scope_and_database_guard(self):
        self.assertEqual(parse_scope('SHIP'),('PLATFORM',[]))
        self.assertEqual(parse_scope('REDEEM'),('PLATFORM',[]))
        self.assertEqual(scope_data({}),{'redeemScope':'PLATFORM','redeemStoreIds':[],'redeemStores':[]})
        payload=self.product_payload(self.leaf()['id'])
        data=self.send(self.owner,'post','/api/v1/admin/products',payload).json()['data']
        from catalog.models import Product
        product=Product.objects.get(pk=data['productId'])
        self.assertEqual(scope_snapshot(product),{'scope':'PLATFORM','stores':[]})
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=product.pk).update(redeem_scope='STORE',redeem_store_ids=[str(uuid4())])
