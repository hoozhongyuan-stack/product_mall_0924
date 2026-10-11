"""Seed synthetic restaurant test prerequisites only in a runner-owned local preview DB.
Run via Django shell with MALL_E2E_PREVIEW=1; credentials stay outside source/output.
"""
import os
import re
from django.conf import settings
from inventory.models import Warehouse
from stores.models import Store
from customers.models import Member
from catalog.models import MemberGrade
from django.db import transaction

db=settings.DATABASES['default']
assert os.environ.get('MALL_E2E_PREVIEW')=='1'
assert re.fullmatch(r'test_health_e2e_[0-9a-f]{16}',db['NAME'])
assert db['HOST'] in ('127.0.0.1','localhost')
assert not any(settings.ORDER_PAYMENT_METHODS_ENABLED.values())
assert not settings.WECHAT_MINI_APP_ID and not settings.WECHAT_MINI_APP_SECRET
with transaction.atomic():
    warehouse,_=Warehouse.objects.get_or_create(code='DINING_E2E',defaults={'name':'餐饮联盟隔离验收仓'})
    Store.objects.update_or_create(warehouse=warehouse,defaults={'name':'餐饮联盟隔离前置仓','contact_name':'验收员','contact_phone':'13800000000','address':'深圳隔离验收街1号','city':'深圳','latitude':'22.500000','longitude':'114.100000','enabled':True,'accepting_orders':True,'supported_modes':['DELIVERY'],'delivery_radius_meters':5000,'delivery_fee_fen':0})
    Member.objects.get_or_create(wechat_app_id='wx0000000000000001',wechat_openid='dining_e2e_registered_member',defaults={'nickname':'餐饮验收店长','grade':MemberGrade.objects.filter(enabled=True).first()})
print('Dining integration prerequisites seeded in the isolated preview database.')
