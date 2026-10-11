"""Order action fixtures, only inside a newly owned localhost preview database."""
import hashlib
import json
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from accounts.models import AdminAccount
from catalog.models import Asset, Category, MemberGrade, Product, Sku, SkuUnitVersion
from customers.models import Member, CustomerAddress
from inventory.models import Warehouse, InventoryBalance
from stores.models import Store, StoreProduct, StoreSkuProfitRule
from payments.models import OfflinePaymentPolicy
from payments.service import VerifiedPayment, record_verified_payment
from checkout.service import create_quote
from orders.models import Order
from orders.service import submit_order, order_data
from fulfillment.store_service import delivery_data, operate
from fulfillment.models import RedeemVoucher
from fulfillment.codes import voucher_code


def seed():
    database = settings.DATABASES['default']
    target = Path(os.environ.get('MALL_E2E_ORDER_FIXTURE_FILE', ''))
    if (os.environ.get('MALL_E2E_ORDER_ACTIONS') != '1' or os.environ.get('MALL_E2E_PREVIEW') != '1'
            or not re.fullmatch(r'test_health_e2e_[0-9a-f]{16}', database['NAME'])
            or database['HOST'] not in ('127.0.0.1', 'localhost')
            or not target.is_absolute() or target.name != 'order-fixtures.json'):
        raise RuntimeError('Order action fixtures require the owned localhost preview runner.')
    if (Product.objects.exists() or Order.objects.exists() or Member.objects.exists()
            or settings.WECHAT_MINI_APP_ID or settings.WECHAT_MINI_APP_SECRET
            or settings.WECHAT_REFUND_ENABLED or settings.EXCHANGE_ORDER_ENABLED
            or settings.KDNIAO['ENABLED'] or any(settings.ORDER_PAYMENT_METHODS_ENABLED.values())):
        raise RuntimeError('Refusing business data or external channel credentials.')
    owner = AdminAccount.objects.get(login_name=os.environ['MALL_E2E_OWNER'], kind='OWNER')
    with transaction.atomic():
        # Only the owned synthetic database enables offline order submission.
        OfflinePaymentPolicy.objects.update_or_create(pk=1, defaults={
            'instructions': '隔离演示：仅用于本地验收，无真实银行收款。',
            'merchant_account_id': 'LOCAL-PREVIEW-ACCOUNT', 'offline_enabled': True, 'wechat_enabled': False})
        warehouse = Warehouse.objects.create(code='ORDER_PREVIEW', name='隔离验收仓', is_default=True)
        store = Store.objects.create(warehouse=warehouse, name='订单操作验收前置仓', contact_name='验收负责人',
            contact_phone='13800000000', address='隔离验收路 1 号', latitude=30, longitude=120,
            supported_modes=['DELIVERY', 'PICKUP', 'EXPRESS'], delivery_radius_meters=5000, delivery_fee_fen=0)
        category = Category.objects.create(name='订单验收商品')
        leaf = Category.objects.create(name='验收样品', parent=category)
        raw = (settings.BASE_DIR.parent / 'mini-program/assets/startup-brand.png').read_bytes()
        stored_name = default_storage.save('product/order-actions-preview.png', ContentFile(raw))
        asset = Asset.objects.create(kind='IMAGE', content_type='image/png', byte_size=len(raw),
            width=128, height=128, sha256=hashlib.sha256(raw).hexdigest(), original_name='隔离占位图.png',
            stored_name=stored_name, created_by=owner)
        skus = {}
        for kind, name in [('SHIP', '本地实物验收商品'), ('REDEEM', '本地核销验收服务')]:
            product = Product.objects.create(product_no='LOCAL-' + kind, name=name, category=leaf,
                status='ON_SALE', fulfillment_kind=kind, ever_on_sale=True, main_image=asset,
                redeem_valid_until=timezone.localdate()+timedelta(days=30) if kind == 'REDEEM' else None)
            sku = Sku.objects.create(product=product, sku_code='LOCAL-' + kind, spec_key='only',
                list_price_fen=1500, sale_status='ON_SALE')
            unit = SkuUnitVersion.objects.create(sku=sku, base_unit='件', sale_unit='件', ratio=1)
            sku.current_unit = unit
            sku.save(update_fields=['current_unit'])
            InventoryBalance.objects.create(warehouse=warehouse, sku=sku, on_hand_base_units=100)
            if kind == 'SHIP':
                StoreProduct.objects.create(store=store, product=product, on_sale=True)
            StoreSkuProfitRule.objects.create(sku=sku, unit_version=unit, purchase_cost_fen=800, platform_share_bps=2000)
            skus[kind] = sku
        member = Member.objects.create(wechat_app_id='wx-isolated-preview', wechat_openid='isolated-preview-buyer',
            nickname='本地验收顾客', grade=MemberGrade.objects.first())
        address = CustomerAddress.objects.create(member=member, recipient_name='验收顾客', phone='13800000000',
            province='浙江', city='杭州', district='西湖区', detail='本地隔离验收地址', latitude=30, longitude=120)

    def create(kind='SHIP', mode='DELIVERY', paid=True):
        body = {'items': [{'skuId': str(skus[kind].id), 'quantity': 2}]}
        if kind == 'SHIP':
            body.update(storeId=str(store.id), deliveryMode=mode)
            if mode != 'PICKUP':
                body['addressId'] = str(address.id)
        quote = create_quote(body, member)
        data, _ = submit_order(member, {'quoteId': quote['quoteId'], 'paymentMethod': 'OFFLINE'}, uuid.uuid4())
        order = Order.objects.get(pk=data['orderId'])
        if paid:
            record_verified_payment(VerifiedPayment(order_no=order.order_no, channel='OFFLINE',
                merchant_account_id='LOCAL-PREVIEW-ACCOUNT', external_trade_no='LOCAL-' + uuid.uuid4().hex,
                amount_fen=order.payable_fen, paid_at=timezone.now(), source='OFFLINE_RECONCILIATION', event_id=uuid.uuid4().hex))
        order.refresh_from_db()
        return order

    pending = create(paid=False)
    preparation = create()
    pickup = create(mode='PICKUP')
    operate(None, store.id, pickup.id, {'action': 'PREPARE', 'expectedRevision': pickup.revision}, uuid.uuid4(), admin_actor=owner)
    pickup.refresh_from_db()
    redemption = create(kind='REDEEM', mode='PICKUP')
    smoke = create()
    voucher = RedeemVoucher.objects.get(order_line__order=redemption)
    fixtures = {
        'pending': order_data(pending), 'preparation': order_data(preparation),
        'pickup': {**order_data(pickup), 'pickupCode': delivery_data(pickup, include_code=True)['pickupCode']},
        'redemption': {**order_data(redemption), 'voucherCode': voucher_code(voucher.order_line_id, voucher.nonce)},
        'apiPrepare': order_data(smoke),
    }
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(fixtures, output, ensure_ascii=False, indent=2)
    print('Created five owned synthetic order fixtures; channel calls remain disabled.')


seed()
