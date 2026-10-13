"""Synthetic governance scenarios inside the preview runner's newly owned DB."""
import hashlib
import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from django.db import transaction
from accounts.models import AdminAccount
from catalog.models import Asset, Category, MemberGrade, Product, Sku, SkuUnitVersion
from customers.models import Member, MemberSession, CustomerAddress
from inventory.models import Warehouse, InventoryBalance
from stores.models import Store, StoreProduct, StoreStaff, StoreSkuProfitRule
from shipping.models import ShippingPolicy
from payments.models import OfflinePaymentPolicy
from payments.service import VerifiedPayment, record_verified_payment
from checkout.service import create_quote
from orders.models import Order
from orders.service import submit_order
from fulfillment.models import Carrier


def seed():
    name = settings.DATABASES['default']['NAME']
    if not name.startswith('test_health_e2e_') or os.environ.get('MALL_FULFILLMENT_PREVIEW') != '1':
        raise RuntimeError('Fulfillment preview requires the owned local database.')
    if Product.objects.exists() or settings.WECHAT_MINI_APP_SECRET or settings.WECHAT_REFUND_ENABLED or settings.KDNIAO['ENABLED']:
        raise RuntimeError('Refusing existing business data or external channels.')
    owner = AdminAccount.objects.get(login_name=os.environ['MALL_E2E_OWNER'])
    OfflinePaymentPolicy.objects.update_or_create(pk=1, defaults={
        'instructions':'本地验收专用，无真实收款。', 'merchant_account_id':'LOCAL-FULFILLMENT',
        'offline_enabled':True, 'wechat_enabled':False})
    with transaction.atomic():
        Carrier.objects.create(code='LOCAL', name='本地验收快递（不连接外部物流）', enabled=True)
        central = Warehouse.objects.create(code='FG-CENTRAL', name='平台总仓', is_default=True)
        front = Warehouse.objects.create(code='FG-FRONT', name='南山前置仓')
        other = Warehouse.objects.create(code='FG-OTHER', name='福田前置仓')
        store = Store.objects.create(warehouse=front, name='南山前置仓', contact_name='本地验收',
            contact_phone='13800000000', address='本地验收路 1 号', latitude=30, longitude=120,
            supported_modes=['DELIVERY','PICKUP'], delivery_fee_fen=600, delivery_radius_meters=5000)
        other_store = Store.objects.create(warehouse=other,name='福田前置仓',supported_modes=['PICKUP'])
        policy = ShippingPolicy.objects.get(pk=1)
        policy.zones=[dict(id=str(uuid.uuid4()),storeId=str(store.id),name='本地配送区',kind='CIRCLE',
            center=dict(latitude=30,longitude=120),radiusMeters=5000,polygon=[],priority=10,
            feeMode='FIXED',feeFen=600,firstMeters=3000,extraMeters=1000,extraFeeFen=200,
            minimumFen=0,freeThresholdFen=None,startTime='00:00',endTime='23:59',enabled=True)]
        policy.save(update_fields=['zones'])
        parent = Category.objects.create(name='履约验收')
        leaf = Category.objects.create(name='验收商品',parent=parent)
        raw=(settings.BASE_DIR.parent/'mini-program/assets/startup-brand.png').read_bytes()
        asset=Asset.objects.create(kind='IMAGE',content_type='image/png',byte_size=len(raw),width=128,height=128,
            sha256=hashlib.sha256(raw).hexdigest(),original_name='本地验收商品.png',created_by=owner,
            stored_name=default_storage.save('product/fulfillment-preview.png',ContentFile(raw)))
        skus={}
        for kind,scope,label in [('SHIP','PLATFORM','实物商品'),('REDEEM','PLATFORM','平台核销权益'),
                                 ('REDEEM','STORE','指定前置仓权益'),('REDEEM','BOTH','两端共享权益')]:
            product=Product.objects.create(product_no='FG-'+(scope if kind=='REDEEM' else 'SHIP'),name=label,
                category=leaf,status='ON_SALE',ever_on_sale=True,fulfillment_kind=kind,main_image=asset,
                redeem_scope=scope,redeem_store_ids=[str(store.id)] if scope!='PLATFORM' else [],
                redeem_valid_until=timezone.localdate()+timedelta(days=30) if kind=='REDEEM' else None)
            sku=Sku.objects.create(product=product,sku_code=product.product_no,spec_key='single',sale_status='ON_SALE',list_price_fen=1500)
            unit=SkuUnitVersion.objects.create(sku=sku,base_unit='件' if kind=='SHIP' else '次',sale_unit='件' if kind=='SHIP' else '次',ratio=1)
            sku.current_unit=unit
            sku.save(update_fields=['current_unit'])
            InventoryBalance.objects.create(warehouse=central,sku=sku,on_hand_base_units=100)
            if kind=='SHIP':
                InventoryBalance.objects.create(warehouse=front,sku=sku,on_hand_base_units=100)
                StoreProduct.objects.create(store=store,product=product,on_sale=True)
                StoreSkuProfitRule.objects.create(sku=sku,unit_version=unit,purchase_cost_fen=800,platform_share_bps=2000)
            skus[kind if kind=='SHIP' else scope]=sku
        member=Member.objects.create(wechat_app_id=settings.WECHAT_MINI_APP_ID,wechat_openid='local-fulfillment-preview',
            nickname='本地验收顾客兼仓工作人员',grade=MemberGrade.objects.first())
        StoreStaff.objects.create(store=store,member=member,permissions=['orders','products','accounts'])
        token,_=MemberSession.issue(member)
        address=CustomerAddress.objects.create(member=member,recipient_name='验收顾客',phone='13800000000',
            province='浙江',city='杭州',district='西湖区',detail='本地隔离验收地址',latitude=30,longitude=120)
    fixtures=[]

    def create(label,keys,mode='REDEEM',quantity=3):
        body={'items':[{'skuId':str(skus[key].id),'quantity':quantity} for key in keys], 'deliveryMode':mode}
        if mode in ('DELIVERY','PICKUP','EXPRESS'):
            body['storeId']=str(store.id)
        if mode in ('DELIVERY','EXPRESS'):
            body['addressId']=str(address.id)
        quote=create_quote(body,member)
        data,_=submit_order(member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid.uuid4())
        order=Order.objects.get(pk=data['orderId'])
        record_verified_payment(VerifiedPayment(order_no=order.order_no,channel='OFFLINE',merchant_account_id='LOCAL-FULFILLMENT',
            external_trade_no='LOCAL-'+uuid.uuid4().hex,amount_fen=order.payable_fen,paid_at=timezone.now(),
            source='OFFLINE_RECONCILIATION',event_id=uuid.uuid4().hex))
        order.refresh_from_db()
        fixtures.append({'orderId':str(order.id),'orderNo':order.order_no,'name':label})
        return order

    create('配送到家 · 待备货',['SHIP'],'DELIVERY')
    create('到仓自提 · 待备货',['SHIP'],'PICKUP')
    create('平台快递 · 待发货',['SHIP'],'EXPRESS')
    create('仅平台核销 · 3次',['PLATFORM'])
    create('仅指定仓核销 · 3次',['STORE'])
    create('两端共享核销 · 3次',['BOTH'])
    create('快递＋共享虚拟权益',['SHIP','BOTH'],'EXPRESS')
    partial=create('配送 · 1件售后冻结／2件可履约',['SHIP'],'DELIVERY')
    from aftersales.service import apply_case
    apply_case(member,partial.lines.get().id,'REFUND_ONLY',1,'本地验收部分冻结',uuid.uuid4())
    payload={'apiBaseUrl':os.environ['MALL_API_PROXY_TARGET'],'memberToken':token,'storeId':str(store.id),
        'otherStoreId':str(other_store.id),'orders':fixtures}
    target=Path(os.environ['MALL_FULFILLMENT_FIXTURE_FILE'])
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as output:
        json.dump(payload,output,ensure_ascii=False,indent=2)
    print('Created eight synthetic governance scenarios; external channels remain disabled.')


seed()
