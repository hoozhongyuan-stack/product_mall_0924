"""Synthetic procurement fixtures, only in a newly owned local integration DB."""
import hashlib
import os
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from catalog.models import Category, Product, Sku, SkuUnitVersion, MemberGrade, SpecAxis, SpecOption, SkuSpecSelection
from customers.models import Member, MemberSession
from inventory.models import Warehouse, InventoryBalance, StockPool, StockPoolSku
from stores.models import Store, StoreStaff, StoreProduct, StoreSkuProfitRule


@transaction.atomic
def seed():
    database = settings.DATABASES['default']
    if (os.environ.get('MALL_E2E_PROCUREMENT') != '1'
            or not re.fullmatch(r'test_health_e2e_[0-9a-f]{16}', database['NAME'])
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise RuntimeError('Procurement fixtures require an owned local integration DB.')
    if (Product.objects.exists() or Member.objects.exists()
            or any(settings.ORDER_PAYMENT_METHODS_ENABLED.values())
            or settings.WECHAT_MINI_APP_SECRET or settings.EXCHANGE_ORDER_ENABLED
            or settings.WECHAT_REFUND_ENABLED or settings.KDNIAO['ENABLED']):
        raise RuntimeError('Refusing business data or external platform credentials.')
    member = Member.objects.create(id=os.environ['MALL_E2E_MEMBER_ID'],
        wechat_openid='procurement-synthetic-only', wechat_app_id=settings.WECHAT_MINI_APP_ID,
        grade=MemberGrade.objects.first(), nickname='采购隔离测试')
    MemberSession.objects.create(member=member, auth_version=member.auth_version,
        token_digest=hashlib.sha256(os.environ['MALL_E2E_MEMBER_TOKEN'].encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(hours=1))
    warehouse = Warehouse.objects.create(id=os.environ['MALL_E2E_WAREHOUSE_ID'],
        code='PROCUREMENT_E2E', name='采购隔离仓')
    store = Store.objects.create(id=os.environ['MALL_E2E_STORE_ID'], warehouse=warehouse,
        name='采购隔离前置仓', address='隔离测试收货地址', contact_name='测试联系人')
    StoreStaff.objects.create(store=store, member=member,
        permissions=['procurement', 'receiving', 'products'])
    root = Category.objects.create(name='采购隔离一级')
    leaf = Category.objects.create(name='采购隔离二级', parent=root)
    product = Product.objects.create(product_no='PROCUREMENT_E2E', name='采购隔离白酒',
        category=leaf, fulfillment_kind='SHIP', status='ON_SALE', ever_on_sale=True)
    axis = SpecAxis.objects.create(product=product, name='包装', sort_order=0)
    skus = []
    for index, (name, ratio, price) in enumerate([('瓶', 1, 6000), ('箱', 6, 36000)]):
        sku = Sku.objects.create(product=product, sku_code=f'PROCUREMENT_E2E_{index}',
            spec_key=name, list_price_fen=price + 1000, sale_status='ON_SALE')
        unit = SkuUnitVersion.objects.create(sku=sku, base_unit='瓶', sale_unit=name, ratio=ratio)
        sku.current_unit = unit
        sku.save(update_fields=['current_unit'])
        option = SpecOption.objects.create(axis=axis, value=name, sort_order=index)
        SkuSpecSelection.objects.create(sku=sku, axis=axis, option=option)
        StoreSkuProfitRule.objects.create(sku=sku, unit_version=unit,
            purchase_cost_fen=price, platform_share_bps=2000)
        skus.append(sku)
    pool = StockPool.objects.create(anchor_sku=skus[0], base_unit='瓶')
    for sku in skus:
        StockPoolSku.objects.create(sku=sku, pool=pool)
    # Synthetic occupancy verifies procurement never releases a sale reservation.
    InventoryBalance.objects.create(warehouse=warehouse, sku=skus[0],
        on_hand_base_units=12, reserved_base_units=3)
    StoreProduct.objects.create(store=store, product=product, on_sale=False)


seed()
