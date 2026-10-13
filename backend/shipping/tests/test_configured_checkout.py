from uuid import uuid4
from datetime import timedelta
from django.db import DatabaseError,transaction
from django.test import TestCase,override_settings
from django.utils import timezone
from orders.tests.test_store_checkout import StoreCheckoutTests
from shipping.models import ShippingPolicy
from shipping.tests.helpers import configure_zone
from checkout.service import create_quote
from orders.service import submit_order,OrderError
from orders.models import Order
from checkout.models import CheckoutQuote
from catalog.models import SkuUnitVersion


@override_settings(ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE':True,'WECHAT':False})
class ConfiguredCheckoutTests(TestCase):
    setUp=StoreCheckoutTests.setUp
    _sku=StoreCheckoutTests._sku
    make_store=StoreCheckoutTests.make_store
    delivery_address=StoreCheckoutTests.delivery_address
    quote=StoreCheckoutTests.quote

    def setup_delivery(self):
        self.store=self.make_store(stock=100)
        self.address=self.delivery_address()
        return configure_zone(self.store,minimumFen=1000,freeThresholdFen=2000)

    def test_coupon_then_points_threshold_and_frozen_order(self):
        self.setup_delivery()
        quote=self.quote(self.store,'DELIVERY',addressId=str(self.address.pk))
        snap=quote['shippingSnapshot']
        self.assertEqual(snap['thresholdAmountFen'],2000)
        self.assertEqual((snap['customerFeeFen'],snap['warehouseBearsFen'],snap['platformBearsFen']),(0,600,0))
        result,_=submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid4())
        order=Order.objects.get(pk=result['orderId'])
        self.assertEqual(order.shipping_snapshot,snap)
        with self.assertRaises(DatabaseError),transaction.atomic():Order.objects.filter(pk=order.pk).update(shipping_snapshot={})
        with self.assertRaises(DatabaseError),transaction.atomic():CheckoutQuote.objects.filter(pk=quote['quoteId']).update(shipping_snapshot={})

    def test_delivery_revision_change_rejects_old_quote(self):
        self.setup_delivery();quote=self.quote(self.store,'DELIVERY',addressId=str(self.address.pk))
        policy=ShippingPolicy.objects.get(pk=1);policy.revision+=1;policy.save()
        with self.assertRaises(OrderError) as caught:submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid4())
        self.assertEqual(caught.exception.code,'SHIPPING_CHANGED')
        self.assertFalse(Order.objects.exists())

    def test_platform_campaign_is_authoritative_and_expiration_rechecked(self):
        self.setup_delivery();policy=ShippingPolicy.objects.get(pk=1);now=timezone.now()
        policy.campaign=dict(enabled=True,name='平台活动',startsAt=(now-timedelta(hours=1)).isoformat(),endsAt=(now+timedelta(hours=1)).isoformat(),minimumFen=0,storeIds=[str(self.store.pk)],deliveryModes=['DELIVERY'])
        policy.save()
        quote=self.quote(self.store,'DELIVERY',addressId=str(self.address.pk))
        self.assertEqual(quote['shippingSnapshot']['platformBearsFen'],600)
        self.assertEqual(quote['shippingSnapshot']['warehouseBearsFen'],0)
        policy.campaign={**policy.campaign,'endsAt':now.isoformat()};policy.save()
        with self.assertRaises(OrderError):submit_order(self.member,{'quoteId':quote['quoteId'],'paymentMethod':'OFFLINE'},uuid4())
        with self.assertRaises(ValueError):create_quote(dict(items=[dict(skuId=str(self.skus[0].pk),quantity=1)],platformFree=True),self.member)

    def test_weight_sale_unit_and_multi_template_group_sums(self):
        self.setup_delivery();policy=ShippingPolicy.objects.get(pk=1)
        policy.templates=[dict(id=str(uuid4()),name='重量',enabled=True,productIds=[],mode='WEIGHT',firstFeeFen=800,firstUnits=1000,extraFeeFen=200,extraUnits=1000,freeThresholdFen=None,regions=[],blockedRegions=[])]
        policy.sku_weights=[dict(skuId=str(self.skus[0].pk),unitVersionId=str(self.skus[0].current_unit_id),weightGrams=1150)]
        policy.save()
        quote=self.quote(self.store,'EXPRESS',addressId=str(self.address.pk))
        self.assertEqual(quote['shippingFeeFen'],1200)
        next_unit=SkuUnitVersion.objects.create(sku=self.skus[0],base_unit='件',sale_unit='箱',ratio=1)
        self.skus[0].current_unit=next_unit;self.skus[0].save()
        changed=self.quote(self.store,'EXPRESS',addressId=str(self.address.pk))
        self.assertFalse(changed['shippingSnapshot']['eligible'])
        self.assertIn('重量',changed['shippingSnapshot']['message'])

    def test_minimum_and_exclusion_block_but_unchosen_address_is_pending(self):
        self.setup_delivery();configure_zone(self.store,minimumFen=2001)
        quote=self.quote(self.store,'DELIVERY',addressId=str(self.address.pk))
        self.assertFalse(quote['orderSubmissionAvailable']);self.assertIn('起送',quote['deliveryMessage'])
        pending=self.quote(self.store,'DELIVERY')
        self.assertTrue(pending['shippingSnapshot']['pending'])
        with self.assertRaises(OrderError):submit_order(self.member,{'quoteId':pending['quoteId'],'paymentMethod':'OFFLINE'},uuid4())

    def test_product_template_groups_sum_and_paid_freight_not_profit(self):
        from catalog.models import Product,Sku,SkuUnitVersion
        from shipping.service import quote_shipping
        self.setup_delivery();policy=ShippingPolicy.objects.get(pk=1)
        second=Product.objects.create(product_no='shipping-second',name='第二商品',category=self.product.category,fulfillment_kind='SHIP',main_image=self.product.main_image,status='ON_SALE',ever_on_sale=True)
        second_sku=Sku.objects.create(product=second,sku_code='SHIP-SECOND',spec_key='default',list_price_fen=1000,sale_status='ON_SALE')
        unit=SkuUnitVersion.objects.create(sku=second_sku,base_unit='件',sale_unit='件',ratio=1)
        second_sku.current_unit=unit;second_sku.save()
        rule=policy.templates[0]
        policy.templates=[dict(rule,firstFeeFen=800),dict(rule,id=str(uuid4()),productIds=[str(second.pk)],firstFeeFen=200)]
        policy.save()
        lines=[dict(skuId=str(self.skus[0].pk),productId=str(self.product.pk),quantity=1,fulfillmentKind='SHIP'),dict(skuId=str(second_sku.pk),productId=str(second.pk),quantity=2,fulfillmentKind='SHIP')]
        snap=quote_shipping(lines,self.store,'EXPRESS',dict(province='湖南',city='长沙',district='岳麓'),2000)
        self.assertEqual(snap['customerFeeFen'],1000)
        self.assertEqual(len(snap['ruleIds']),2)

    def test_coupon_reduces_threshold_but_points_do_not(self):
        from orders.tests.test_order_flow import OrderFlowTests
        from benefits.service import grant_points
        self.setup_delivery()
        coupon=OrderFlowTests._cash_coupon(self,discount=300)
        grant_points(self.member,200,timezone.now()+timedelta(days=1),'shipping-points')
        quote=self.quote(self.store,'DELIVERY',addressId=str(self.address.pk),couponId=str(coupon.pk),pointsToUse=100)
        self.assertEqual(quote['shippingSnapshot']['thresholdAmountFen'],1700)
        self.assertEqual(quote['shippingFeeFen'],600)
        configure_zone(self.store,freeThresholdFen=1700)
        quote=self.quote(self.store,'DELIVERY',addressId=str(self.address.pk),couponId=str(coupon.pk),pointsToUse=100)
        self.assertEqual(quote['shippingSnapshot']['warehouseBearsFen'],600)

    def test_weight_targets_expose_unit_and_reject_stale_unit_save(self):
        from catalog.shipping_access import shipping_targets
        from shipping.configuration import weights
        from shipping.rules import ShippingRuleError
        self.setup_delivery()
        target=shipping_targets('ORD-A',1,20,[])['items'][0]['skus'][0]
        self.assertEqual(target['unitVersionId'],str(self.skus[0].current_unit_id))
        valid=weights([dict(skuId=str(self.skus[0].pk),weightGrams=800,expectedUnitVersionId=target['unitVersionId'])])
        self.assertEqual(valid[0]['weightGrams'],800)
        with self.assertRaises(ShippingRuleError):weights([dict(skuId=str(self.skus[0].pk),weightGrams=800,expectedUnitVersionId=str(uuid4()))])

    def test_platform_express_without_store_does_not_charge_warehouse(self):
        self.setup_delivery()
        quote=create_quote(dict(items=[dict(skuId=str(self.skus[0].pk),quantity=2)],addressId=str(self.address.pk)),self.member)
        snap=quote['shippingSnapshot']
        self.assertTrue(snap['eligible']);self.assertFalse(snap['pending'])
        self.assertEqual(snap['warehouseBearsFen'],0)
        result,_=submit_order(self.member,dict(quoteId=quote['quoteId'],paymentMethod='OFFLINE'),uuid4())
        order=Order.objects.get(pk=result['orderId'])
        self.assertIsNone(order.store_id)
        self.assertEqual(order.delivery_mode,'EXPRESS')

    def test_platform_subsidy_larger_than_commission_persists_conserved_income(self):
        from stores.models import StoreSkuProfitRule
        from payments.store_finance import order_split
        from payments.models import StoreIncome
        self.setup_delivery();policy=ShippingPolicy.objects.get(pk=1);now=timezone.now()
        policy.campaign=dict(enabled=True,name='平台包邮',startsAt=(now-timedelta(hours=1)).isoformat(),endsAt=(now+timedelta(hours=1)).isoformat(),minimumFen=0,storeIds=[],deliveryModes=['DELIVERY'])
        policy.save()
        StoreSkuProfitRule.objects.create(sku=self.skus[0],unit_version=self.skus[0].current_unit,purchase_cost_fen=600,platform_share_bps=1000)
        quote=self.quote(self.store,'DELIVERY',addressId=str(self.address.pk))
        result,_=submit_order(self.member,dict(quoteId=quote['quoteId'],paymentMethod='OFFLINE'),uuid4())
        order=Order.objects.get(pk=result['orderId']);split=order_split(order)
        self.assertEqual(split['platform_fen'],-520)
        self.assertEqual(split['store_fen'],1920)
        income=StoreIncome.objects.create(order=order,store=self.store,status='PENDING',**split)
        income.refresh_from_db();self.assertEqual(income.platform_fen,-520)
        self.assertEqual(income.paid_fen,income.platform_fen+income.freight_fen+income.store_fen+income.dining_commission_fen)
