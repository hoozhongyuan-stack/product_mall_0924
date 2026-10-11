import json
import uuid
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.test import TestCase, Client
from django.db import transaction, DatabaseError
from catalog.models import Category, Product, Sku, SkuUnitVersion, MemberGrade
from customers.models import Member
from accounts.models import AdminAccount
from stores.models import Store, StoreStaff, StoreProduct, StoreSkuProfitRule
from stores.access import StoreError
from inventory.models import Warehouse, InventoryBalance, InventoryLedger, StockPool, StockPoolSku
from stores.procurement.service import create_order, transition, data
from stores.procurement.models import ProcurementOrder, ProcurementSettings, ProcurementProof, ProcurementEvent


class ProcurementFixture:
    @transaction.atomic
    def setup_fixture(self):
        self.store=Store.objects.create(name='采购仓',warehouse=Warehouse.objects.create(code='BUY',name='采购仓'))
        self.other=Store.objects.create(name='其他仓',warehouse=Warehouse.objects.create(code='OTHER',name='其他仓'))
        grade=MemberGrade.objects.first() or MemberGrade.objects.create(code='PROCURE_TEST',name='普通会员',rank=0)
        self.member=Member.objects.create(wechat_openid='buy',wechat_app_id='test',grade=grade)
        self.staff=StoreStaff.objects.create(store=self.store,member=self.member,permissions=['procurement','receiving'])
        self.product=Product.objects.create(name='商品',product_no='BUY-P',category=Category.objects.create(name='类目'),fulfillment_kind='SHIP',status='ON_SALE',ever_on_sale=True)
        self.sku=Sku.objects.create(product=self.product,sku_code='BUY-S',spec_key='瓶',list_price_fen=100,sale_status='ON_SALE')
        self.unit=SkuUnitVersion.objects.create(sku=self.sku,base_unit='瓶',sale_unit='瓶',ratio=1)
        self.sku.current_unit=self.unit;self.sku.save()
        self.cost=StoreSkuProfitRule.objects.create(sku=self.sku,unit_version=self.unit,purchase_cost_fen=60,platform_share_bps=2000)
        self.owner=AdminAccount.objects.create_user('procure-owner','Procure safe phrase 2026',kind='OWNER')
        self.cipher=Fernet(Fernet.generate_key())
        self.patch=patch('stores.procurement.service.cipher',return_value=self.cipher);self.patch.start();self.addCleanup(self.patch.stop)
        ProcurementSettings.objects.get_or_create(pk=1)
        ProcurementSettings.objects.filter(pk=1).update(encrypted_instructions=self.cipher.encrypt('演示平台采购收款说明'.encode()).decode())

    def body(self):
        return {'requestKey':str(uuid.uuid4()),'lines':[{'skuId':str(self.sku.pk),'quantity':2,'expectedCostRevision':1,'expectedUnitVersionId':str(self.unit.pk)}],'note':''}

    def order(self):
        return create_order(self.store,self.member,self.body())

    def move(self,row,action,admin=False,**extra):
        return transition(row,self.owner if admin else self.member,action,{'requestKey':str(uuid.uuid4()),'expectedRevision':row.revision,**extra},admin)

    def shipped(self):
        row=self.move(self.order(),'review',True,freightFen=10)
        row=self.move(row,'confirm-quote')
        proof=ProcurementProof.objects.create(store=self.store,member=self.member,stored_name='product/procurement-proofs/test.png',content_type='image/png',byte_size=1,sha256='0'*64)
        row=self.move(row,'payment-proof',assetId=str(proof.pk),note='已转账')
        row=self.move(row,'confirm-payment',True,paymentReference='BANK-1',note='实际到账')
        return self.move(row,'ship',True,carrier='演示物流',tracking='TRACK-1')


class ProcurementTests(ProcurementFixture,TestCase):
    def setUp(self):self.setup_fixture()

    def test_create_snapshot_idempotent_down_store_listing_allowed(self):
        StoreProduct.objects.create(store=self.store,product=self.product,on_sale=False)
        body=self.body();row=create_order(self.store,self.member,body)
        self.assertEqual(row.goods_amount_fen,120)
        self.assertEqual(row.lines.get().ratio,1)
        self.assertFalse(InventoryLedger.objects.exists())
        self.assertEqual(create_order(self.store,self.member,body).pk,row.pk)
        self.cost.purchase_cost_fen=99;self.cost.save()
        self.assertEqual(row.lines.get().purchase_cost_fen,60)
        changed={**body,'note':'changed'}
        with self.assertRaises(StoreError):create_order(self.store,self.member,changed)

    def test_create_validation_and_permissions(self):
        for change in [{'quantity':0},{'quantity':True},{'quantity':1000},{'expectedCostRevision':2},{'expectedUnitVersionId':str(uuid.uuid4())}]:
            body=self.body();body['lines'][0].update(change)
            with self.assertRaises(StoreError):create_order(self.store,self.member,body)
        for lines in [[],[self.body()['lines'][0]]*2]:
            body=self.body();body['lines']=lines
            with self.assertRaises(StoreError):create_order(self.store,self.member,body)
        self.staff.permissions=['products'];self.staff.save()
        with self.assertRaises(StoreError):self.order()

    def test_unavailable_sku_and_store(self):
        self.product.status='OFF_SALE';self.product.save()
        with self.assertRaises(StoreError):self.order()
        self.product.status='ON_SALE';self.product.save()
        self.store.enabled=False;self.store.save()
        with self.assertRaises(StoreError):self.order()

    def test_quote_requires_settings_and_freezes_instructions(self):
        row=self.order()
        ProcurementSettings.objects.filter(pk=1).update(encrypted_instructions='')
        with self.assertRaises(StoreError):self.move(row,'review',True,freightFen=1)
        ProcurementSettings.objects.filter(pk=1).update(encrypted_instructions=self.cipher.encrypt(b'original').decode())
        row=self.move(row,'review',True,freightFen=10)
        ProcurementSettings.objects.filter(pk=1).update(encrypted_instructions=self.cipher.encrypt(b'new').decode())
        self.assertEqual(data(row)['paymentInstructions'],'')
        row=self.move(row,'confirm-quote')
        self.assertEqual(data(row)['paymentInstructions'],'original')
        self.assertEqual(data(row)['payableFen'],130)

    def test_received_adds_stock_once_preserves_reserve_terminal(self):
        balance=InventoryBalance.objects.create(warehouse=self.store.warehouse,sku=self.sku,on_hand_base_units=12,reserved_base_units=3)
        row=self.shipped();body={'requestKey':str(uuid.uuid4()),'expectedRevision':row.revision,'lineIds':[str(row.lines.get().pk)]}
        row=transition(row,self.member,'receive',body)
        self.assertEqual(transition(row,self.member,'receive',body).status,'RECEIVED')
        balance.refresh_from_db();self.assertEqual((balance.on_hand_base_units,balance.reserved_base_units),(14,3))
        self.assertEqual(InventoryLedger.objects.filter(movement_type='PROCURE').count(),1)
        with self.assertRaises(StoreError):self.move(row,'cancel',reason='cancel')
        with self.assertRaises(StoreError):self.move(row,'receive',lineIds=body['lineIds'])

    def test_shared_case_uses_frozen_factor(self):
        pool=StockPool.objects.create(anchor_sku=self.sku,base_unit='瓶');StockPoolSku.objects.create(sku=self.sku,pool=pool)
        case=Sku.objects.create(product=self.product,sku_code='CASE',spec_key='箱',list_price_fen=600,sale_status='ON_SALE')
        unit=SkuUnitVersion.objects.create(sku=case,base_unit='瓶',sale_unit='箱',ratio=6);case.current_unit=unit;case.save()
        StockPoolSku.objects.create(sku=case,pool=pool)
        StoreSkuProfitRule.objects.create(sku=case,unit_version=unit,purchase_cost_fen=360,platform_share_bps=2000)
        body=self.body();body['lines'].append({'skuId':str(case.pk),'quantity':3,'expectedCostRevision':1,'expectedUnitVersionId':str(unit.pk)})
        row=create_order(self.store,self.member,body)
        row.status='SHIPPED';row.save()
        row=self.move(row,'receive',lineIds=[str(x.pk) for x in row.lines.all()])
        balance=InventoryBalance.objects.get(warehouse=self.store.warehouse)
        self.assertEqual(balance.on_hand_base_units,20)
        self.assertEqual(InventoryLedger.objects.count(),2)

    def test_exception_requires_resolution_evidence(self):
        row=self.shipped();row=self.move(row,'exception',note='短缺')
        with self.assertRaises(StoreError):self.move(row,'receive',lineIds=[str(row.lines.get().pk)])
        with self.assertRaises(StoreError):self.move(row,'resolve-exception',True,note='补齐',reference='')
        row=self.move(row,'resolve-exception',True,note='补齐',reference='REPLACE-1')
        self.assertEqual(row.status,'SHIPPED')
        self.assertEqual([x['note'] for x in data(row)['records'] if x['kind']=='exception'],['短缺'])

    def test_payment_without_proof_cancel_requires_reconciliation(self):
        row=self.move(self.move(self.order(),'review',True,freightFen=0),'confirm-quote')
        row=self.move(row,'cancel',reason='可能已经转账')
        self.assertEqual(row.status,'CANCEL_REVIEW')
        with self.assertRaises(StoreError):self.move(row,'close-cancel',True,reference='R',note='退款',refundConfirmed=False)
        row=self.move(row,'close-cancel',True,reference='R',note='核清并退款',refundConfirmed=True)
        self.assertEqual(row.status,'CANCELLED');self.assertEqual(row.events.last().reference,'R')

    def test_unpaid_reject_and_cancel(self):
        self.assertEqual(self.move(self.order(),'reject',True,reason='缺货').status,'CANCELLED')
        self.assertEqual(self.move(self.order(),'cancel',reason='不进货').status,'CANCELLED')

    def test_stale_revision_wrong_action_and_key_collision(self):
        row=self.order();body={'requestKey':str(uuid.uuid4()),'expectedRevision':1,'freightFen':10}
        row=transition(row,self.owner,'review',body,True)
        with self.assertRaises(StoreError):transition(row,self.owner,'review',{**body,'freightFen':11},True)
        with self.assertRaises(StoreError):self.move(row,'ship',True,carrier='x',tracking='x')
        with self.assertRaises(StoreError):transition(row,self.member,'confirm-quote',{'expectedRevision':1,'requestKey':str(uuid.uuid4())})

    def test_unit_change_recheck_uses_original_factor_and_second_change_blocks(self):
        row=self.shipped()
        changed=SkuUnitVersion.objects.create(sku=self.sku,base_unit='瓶',sale_unit='箱',ratio=6)
        Sku.objects.filter(pk=self.sku.pk).update(current_unit=changed)
        ids=[str(row.lines.get().pk)]
        with self.assertRaises(StoreError):self.move(row,'receive',lineIds=ids)
        row=self.move(row,'verify-units',True,note='按原瓶验收',reference='CHECK-1')
        self.assertFalse(data(row)['unitReviewRequired'])
        later=SkuUnitVersion.objects.create(sku=self.sku,base_unit='瓶',sale_unit='箱',ratio=12)
        Sku.objects.filter(pk=self.sku.pk).update(current_unit=later)
        with self.assertRaises(StoreError):self.move(row,'receive',lineIds=ids)
        row=self.move(row,'verify-units',True,note='再次核对原瓶',reference='CHECK-2')
        self.move(row,'receive',lineIds=ids)
        self.assertEqual(InventoryBalance.objects.get(warehouse=self.store.warehouse).on_hand_base_units,2)

    def test_invalid_receive_list_and_cross_member_proof(self):
        row=self.shipped()
        for ids in [[],[{}],['bad'],[str(row.lines.get().pk)]*2]:
            with self.assertRaises(StoreError):self.move(row,'receive',lineIds=ids)
        row=self.move(row,'cancel',reason='cancel')
        row=self.move(row,'close-cancel',True,note='clear',reference='R',refundConfirmed=True)
        with self.assertRaises(StoreError):self.move(row,'payment-proof',assetId=str(uuid.uuid4()),note='bad')

    def test_frozen_line_and_event_database_guards(self):
        row=self.order()
        for query,values in [(row.lines,{'quantity':99}),(row.events,{'note':'tampered'})]:
            with self.assertRaises(DatabaseError),transaction.atomic():query.update(**values)

    def test_reject_proof_and_proof_ownership(self):
        row=self.move(self.move(self.order(),'review',True,freightFen=1),'confirm-quote')
        with self.assertRaises(StoreError):self.move(row,'payment-proof',assetId=str(uuid.uuid4()),note='')
        proof=ProcurementProof.objects.create(store=self.other,member=self.member,stored_name='product/other-proof.png',content_type='image/png',byte_size=1,sha256='0'*64)
        with self.assertRaises(StoreError):self.move(row,'payment-proof',assetId=str(proof.pk),note='')
        proof.store=self.store;proof.save()
        row=self.move(row,'payment-proof',assetId=str(proof.pk),note='凭证')
        row=self.move(row,'reject-proof',True,reason='无法核实')
        self.assertEqual(row.status,'PAYMENT');self.assertIsNone(row.proof_id)
        self.assertEqual(data(row)['records'][-2]['proofAssetId'],str(proof.pk))

    def test_scalar_validation_corrupt_settings_and_permission_denial(self):
        from stores.procurement.service import text, integer, fields, identifier, seal, instructions
        for value in [None,{},123,'bad']:
            with self.assertRaises(StoreError):identifier(value)
        for value in [None,'x'*301]:
            with self.assertRaises(StoreError):text(value)
        with self.assertRaises(StoreError):fields({},['a'])
        with self.assertRaises(StoreError):integer(True)
        with self.assertRaises(StoreError):instructions('corrupt')
        with patch('stores.procurement.service.cipher',side_effect=ValueError('bad')):
            with self.assertRaises(StoreError):seal('secret')
        row=self.order();self.owner.enabled=False;self.owner.save()
        with self.assertRaises(StoreError):self.move(row,'review',True,freightFen=1)
        with self.assertRaises(StoreError):self.move(row,'unknown',True)
        self.owner.enabled=True;self.owner.save()
        self.cost.delete()
        with self.assertRaises(StoreError):self.order()

    def test_inventory_entry_rechecks_version_pool_and_rolls_back(self):
        from inventory.procurement import receive_procurement
        row=self.shipped()
        changed=SkuUnitVersion.objects.create(sku=self.sku,base_unit='瓶',sale_unit='箱',ratio=6)
        Sku.objects.filter(pk=self.sku.pk).update(current_unit=changed)
        with self.assertRaises(StoreError):receive_procurement(row,self.member)
        self.assertFalse(InventoryLedger.objects.exists())
        bad=SkuUnitVersion.objects.create(sku=self.sku,base_unit='盒',sale_unit='盒',ratio=1)
        Sku.objects.filter(pk=self.sku.pk).update(current_unit=bad)
        with self.assertRaises(StoreError):self.move(row,'verify-units',True,note='bad base',reference='R')
        Sku.objects.filter(pk=self.sku.pk).update(current_unit=self.unit)
        InventoryBalance.objects.create(warehouse=self.store.warehouse,sku=self.sku,on_hand_base_units=1000000000000)
        with self.assertRaises(StoreError):self.move(row,'receive',lineIds=[str(row.lines.get().pk)])
        row.refresh_from_db();self.assertEqual(row.status,'SHIPPED');self.assertFalse(InventoryLedger.objects.exists())


class ProcurementApiTests(ProcurementFixture,TestCase):
    def setUp(self):
        self.setup_fixture()
        self.client=Client(enforce_csrf_checks=True);self.client.get('/api/v1/admin/auth/csrf')
        self.send('post','/api/v1/admin/auth/login',{'loginName':self.owner.login_name,'password':'Procure safe phrase 2026'})
        self.base='/api/v1/admin/stores/procurement'
        self.app=f'/api/v1/app/store-center/stores/{self.store.pk}/procurement'

    def send(self,verb,path,body,**headers):
        return getattr(self.client,verb)(path,data=json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value,**headers)

    def confirm(self,action,target,revision):
        result=self.send('post','/api/v1/admin/auth/confirm',{'action':action,'objectId':str(target),'revision':revision,'password':'Procure safe phrase 2026'})
        self.assertEqual(result.status_code,200,result.content)
        return result.json()['data']['confirmationToken']

    def test_settings_private_password_revision_audit(self):
        body={'paymentInstructions':'真实独立采购说明','expectedRevision':0}
        self.assertEqual(self.send('put',self.base+'/settings',body).status_code,403)
        token=self.confirm('stores.procurement.configure','procurement-settings',0)
        result=self.send('put',self.base+'/settings',body,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['data']['revision'],1)
        self.assertNotIn('真实独立采购说明',ProcurementSettings.objects.get(pk=1).encrypted_instructions)
        self.assertEqual(self.send('put',self.base+'/settings',body).status_code,409)
        self.assertIn('no-store',self.client.get(self.base+'/settings')['Cache-Control'])

    def test_admin_list_detail_recovery_csrf_and_finance_confirmation(self):
        row=self.shipped()
        result=self.client.get(self.base+'/orders');self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(self.client.get(self.base+'/orders/'+str(row.pk)).status_code,200)
        key=row.events.filter(kind='ship').get().request_key
        recovered=self.client.get(f'{self.base}/orders/{row.pk}/operations/{key}')
        self.assertEqual(recovered.json()['data']['status'],'COMPLETED')
        self.assertEqual(self.client.get(f'{self.base}/orders/{row.pk}/operations/{uuid.uuid4()}').json()['data']['status'],'NOT_FOUND')
        self.assertEqual(Client().get(self.base+'/orders').status_code,401)
        self.assertEqual(self.client.post(f'{self.base}/orders/{row.pk}/cancel',data='{}',content_type='application/json').status_code,403)
        row=self.move(row,'cancel',reason='退货')
        body={'expectedRevision':row.revision,'requestKey':str(uuid.uuid4()),'reference':'REFUND','note':'全额核清','refundConfirmed':True}
        path=f'{self.base}/orders/{row.pk}/close-cancel'
        self.assertEqual(self.send('post',path,body).status_code,403)
        token=self.confirm('stores.procurement.close-cancel',row.pk,row.revision)
        result=self.send('post',path,body,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(self.send('post',path,body).status_code,200)

    def test_member_scope_catalog_and_create(self):
        with patch('stores.procurement.views.require_member',return_value=(self.member,None)):
            result=self.client.get(self.app+'/products');self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(result.json()['data']['items'][0]['skus'][0]['purchaseCostFen'],60)
            result=self.send('post',self.app+'/orders',self.body());self.assertEqual(result.status_code,201,result.content)
            self.assertEqual(self.client.get(self.app+'/orders').status_code,200)
            self.assertEqual(self.client.get(self.app+'/orders/'+result.json()['data']['id']).status_code,200)
            self.assertEqual(self.client.get(self.app.replace(str(self.store.pk),str(self.other.pk))+'/orders').status_code,403)
            self.staff.permissions=['receiving'];self.staff.save()
            self.assertEqual(self.client.get(self.app+'/orders').status_code,200)
            self.assertEqual(self.client.get(self.app+'/products').status_code,403)
            self.assertEqual(self.send('post',self.app+'/orders',self.body()).status_code,403)

    def test_private_proof_upload_bind_access_and_invalid_files(self):
        import tempfile
        from pathlib import Path
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from catalog.tests.test_media_flow import png
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch('stores.procurement.proofs.require_member',return_value=(self.member,None)):
            result=self.client.post(self.app+'/proofs',{'file':SimpleUploadedFile('proof.png',png())})
            self.assertEqual(result.status_code,201,result.content)
            proof_id=result.json()['data']['assetId'];preview=result.json()['data']['previewUrl']
            image=self.client.get(preview);self.assertEqual(image.status_code,200);b"".join(image.streaming_content)
            self.assertEqual(self.client.get(self.base+'/proofs/'+proof_id+'/file').status_code,403)
            row=self.move(self.move(self.order(),'review',True,freightFen=0),'confirm-quote')
            row=self.move(row,'payment-proof',assetId=proof_id,note='凭证')
            image=self.client.get(self.base+'/proofs/'+proof_id+'/file');self.assertEqual(image.status_code,200);b"".join(image.streaming_content)
            other=Member.objects.create(wechat_openid='other',wechat_app_id='test',grade=MemberGrade.objects.first())
            StoreStaff.objects.create(store=self.store,member=other,permissions=['procurement'])
            unbound=ProcurementProof.objects.create(store=self.store,member=self.member,stored_name='product/unbound.png',content_type='image/png',byte_size=1,sha256='0'*64)
            with patch('stores.procurement.proofs.require_member',return_value=(other,None)):
                self.assertEqual(self.client.get(preview).status_code,200)
                self.assertEqual(self.client.get(self.app+'/proofs/'+str(unbound.pk)+'/file').status_code,403)
                self.assertEqual(self.client.get(self.app.replace(str(self.store.pk),str(self.other.pk))+'/proofs/'+proof_id+'/file').status_code,403)
            bad=self.client.post(self.app+'/proofs',{'file':SimpleUploadedFile('bad.svg',b'<svg>bad</svg>')})
            self.assertEqual(bad.status_code,400,bad.content)
            self.assertEqual(len(list((Path(directory)/'product'/'procurement-proofs').iterdir())),1)
            self.assertEqual(self.client.post(self.app+'/proofs',{'file':SimpleUploadedFile('zero.png',b'')}).status_code,400)
            self.assertEqual(self.client.post(self.app+'/proofs',{'extra':'bad','file':SimpleUploadedFile('a.png',png())}).status_code,400)
            self.assertEqual(self.client.post(self.app+'/proofs',{'file':SimpleUploadedFile('huge.png',b'x'*(5*1024*1024+1))}).status_code,400)
            self.assertEqual(self.client.get(self.app+'/proofs/'+str(uuid.uuid4())+'/file').status_code,404)

    def test_member_actions_and_finance_receipt_replay(self):
        row=self.order();row=self.move(row,'review',True,freightFen=3)
        with patch('stores.procurement.views.require_member',return_value=(self.member,None)):
            body={'expectedRevision':row.revision,'requestKey':str(uuid.uuid4())}
            result=self.send('post',self.app+'/orders/'+str(row.pk)+'/confirm-quote',body)
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(self.send('post',self.app+'/orders/'+str(row.pk)+'/ship',body).status_code,400)
        proof=ProcurementProof.objects.create(store=self.store,member=self.member,stored_name='product/proof.png',content_type='image/png',byte_size=1,sha256='0'*64)
        row.refresh_from_db();row=self.move(row,'payment-proof',assetId=str(proof.pk),note='')
        body={'expectedRevision':row.revision,'requestKey':str(uuid.uuid4()),'paymentReference':'BANK','note':'到账'}
        path=f'{self.base}/orders/{row.pk}/confirm-payment'
        self.assertEqual(self.send('post',path,body).status_code,403)
        token=self.confirm('stores.procurement.confirm-payment',row.pk,row.revision)
        result=self.send('post',path,body,HTTP_X_ACTION_CONFIRMATION=token)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(self.send('post',path,body).status_code,200)
        self.assertEqual(self.send('post',path,{**body,'note':'changed'}).status_code,409)

    def test_api_errors_and_quota(self):
        self.assertEqual(self.client.get(self.base+'/orders?page=0').status_code,400)
        self.assertEqual(self.client.get(self.base+'/orders?q='+'x'*121).status_code,400)
        self.assertEqual(self.client.get(self.base+'/orders/'+str(uuid.uuid4())).status_code,404)
        self.assertEqual(self.send('post',self.base+'/orders/'+str(uuid.uuid4())+'/fake',{}).status_code,400)
        with patch('stores.quota.allowed',return_value=False):self.assertEqual(self.client.get(self.base+'/orders').status_code,429)
        self.assertEqual(self.client.get(self.app+'/orders').status_code,401)

    def test_proof_quota_only_unbound_and_gif_is_rejected(self):
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from catalog.tests.test_media_flow import png
        row=self.order()
        bound=[ProcurementProof(store=self.store,member=self.member,stored_name=f'product/bound-{i}.png',content_type='image/png',byte_size=1,sha256='0'*64) for i in range(100)]
        ProcurementProof.objects.bulk_create(bound)
        ProcurementEvent.objects.bulk_create([ProcurementEvent(order=row,member=self.member,request_key=uuid.uuid4(),digest='0'*64,kind='payment-proof',proof=p) for p in bound])
        with tempfile.TemporaryDirectory() as directory,override_settings(MEDIA_ROOT=directory),patch('stores.procurement.proofs.require_member',return_value=(self.member,None)):
            result=self.client.post(self.app+'/proofs',{'file':SimpleUploadedFile('new.png',png())})
            self.assertEqual(result.status_code,201,result.content)
            bad=self.client.post(self.app+'/proofs',{'file':SimpleUploadedFile('bad.gif',b'GIF89a'+b'x'*40)})
            self.assertEqual(bad.status_code,400,bad.content)
            ProcurementProof.objects.bulk_create([ProcurementProof(store=self.store,member=self.member,stored_name=f'product/unbound-{i}.png',content_type='image/png',byte_size=1,sha256='0'*64) for i in range(99)])
            denied=self.client.post(self.app+'/proofs',{'file':SimpleUploadedFile('blocked.png',png())})
            self.assertEqual(denied.status_code,409,denied.content)
            self.assertEqual(denied.json()['error']['code'],'MEDIA_QUOTA')
