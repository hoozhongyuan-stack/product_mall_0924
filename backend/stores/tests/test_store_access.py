from django.test import TestCase
from catalog.models import Category, Product, Sku, SkuUnitVersion, MemberGrade
from customers.models import Member
from inventory.models import Warehouse, InventoryBalance
from stores.models import Store, StoreStaff, StoreProduct
from stores.access import StoreError, require_staff, get_store, sale_product_ids
from inventory.store_access import set_available_stock, available_base_units


class StoreAccessTests(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name='前置仓', warehouse=Warehouse.objects.create(code='STORE-A',name='前置仓'))
        self.other = Store.objects.create(name='其他', warehouse=Warehouse.objects.create(code='STORE-B',name='其他'))
        self.member = Member.objects.create(wechat_openid='store-staff',wechat_app_id='test',grade=MemberGrade.objects.first())
        self.staff = StoreStaff.objects.create(store=self.store,member=self.member,permissions=['products'])
        category = Category.objects.create(name='酒')
        self.product = Product.objects.create(name='酒',product_no='P',category=category,fulfillment_kind='SHIP',status='ON_SALE',ever_on_sale=True)
        self.sku = Sku.objects.create(product=self.product,sku_code='S',spec_key='瓶',list_price_fen=100,sale_status='ON_SALE')
        unit = SkuUnitVersion.objects.create(sku=self.sku,base_unit='瓶',sale_unit='瓶',ratio=1)
        self.sku.current_unit = unit
        self.sku.save()
        StoreProduct.objects.create(store=self.store,product=self.product,on_sale=True)

    def test_scope_and_permission_are_server_checked(self):
        self.assertEqual(require_staff(self.member,self.store.id,'products'),self.store)
        for store,permission in [(self.other,'products'),(self.store,'accounts')]:
            with self.assertRaises(StoreError):
                require_staff(self.member,store.id,permission)

    def test_pause_blocks_new_sale_but_allows_existing_staff_operations(self):
        self.store.accepting_orders = False
        self.store.save()
        require_staff(self.member,self.store.id,'products')
        with self.assertRaises(StoreError):
            get_store(self.store.id,for_sale=True)

    def test_stock_set_preserves_reserved_and_is_store_scoped(self):
        balance = InventoryBalance.objects.create(warehouse=self.store.warehouse,sku=self.sku,on_hand_base_units=12,reserved_base_units=2)
        set_available_stock(self.store,self.sku.id,7,10,self.member)
        balance.refresh_from_db()
        self.assertEqual((balance.on_hand_base_units,balance.reserved_base_units),(9,2))
        self.assertEqual(available_base_units(self.other,[self.sku.id])[self.sku.id],0)
        with self.assertRaises(StoreError):
            set_available_stock(self.store,self.sku.id,9,10,self.member)

    def test_store_down_only_hides_its_product(self):
        self.assertIn(self.product.id,sale_product_ids(self.store))
        StoreProduct.objects.filter(store=self.store,product=self.product).update(on_sale=False)
        self.assertNotIn(self.product.id,sale_product_ids(self.store))
        self.product.refresh_from_db()
        self.assertEqual(self.product.status,'ON_SALE')

    def test_shared_pool_is_one_store_balance_and_immutable_ledger(self):
        from inventory.models import StockPool,StockPoolSku,InventoryLedger
        from django.db import DatabaseError,transaction
        pool=StockPool.objects.create(anchor_sku=self.sku,base_unit='瓶')
        StockPoolSku.objects.create(sku=self.sku,pool=pool)
        case=Sku.objects.create(product=self.product,sku_code='CASE',spec_key='箱',list_price_fen=1000)
        unit=SkuUnitVersion.objects.create(sku=case,base_unit='瓶',sale_unit='箱',ratio=12)
        case.current_unit=unit;case.save()
        StockPoolSku.objects.create(sku=case,pool=pool)
        set_available_stock(self.store,case.id,24,0,self.member)
        values=available_base_units(self.store,[case.id,self.sku.id])
        self.assertEqual(values,{case.id:24,self.sku.id:24})
        self.assertEqual(InventoryBalance.objects.filter(warehouse=self.store.warehouse).count(),1)
        with self.assertRaises(DatabaseError),transaction.atomic():
            InventoryLedger.objects.filter(movement_type='STORE_SET').update(reason='changed')

    def test_auth_version_revocation_and_invalid_stock(self):
        Member.objects.filter(pk=self.member.pk).update(auth_version=2)
        with self.assertRaises(StoreError):require_staff(self.member,self.store.id,'products')
        for value in (-1,True,1.2,1000000001):
            with self.assertRaises(StoreError):set_available_stock(self.store,self.sku.id,value,0,self.member)
