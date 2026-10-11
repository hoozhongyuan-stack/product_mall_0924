"""Ordinary browsing keeps the authenticated scanned restaurant boundary."""
from copy import deepcopy
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from dining_alliance.models import DiningSession, RestaurantProduct
from orders.tests import test_dining_checkout as fixture
from pages.models import MicroPage, PageConfigVersion, PagePublication


@override_settings(WECHAT_MINI_APP_ID='wx-order-test',ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE':True})
class DiningPublicBrowsingTests(TestCase):
    setUp=fixture.DiningCheckoutTests.setUp
    _sku=fixture.DiningCheckoutTests._sku
    make_store=fixture.DiningCheckoutTests.make_store
    scene=fixture.DiningCheckoutTests.scene

    def get(self,path,identity,token=None,**extra):
        return self.client.get(path,{'diningSessionId':identity,**extra},HTTP_AUTHORIZATION=f'Bearer {token or self.token}')

    def publish(self,kind):
        page=MicroPage.objects.get(page_type='HOME') if kind=='HOME' else MicroPage.objects.create(page_type='MICRO',name='活动')
        config={'schemaVersion':1,'pageType':kind,'theme':{},'components':[{'componentId':'goods','type':'PRODUCT_LIST','visible':True,'props':{'source':'MANUAL','productIds':[str(self.product.id)],'categoryId':'','sort':'PRICE_ASC','limit':1}}]}
        version=PageConfigVersion.objects.create(page=page,revision=1,config_json=config,published_by=self.owner)
        PagePublication.objects.update_or_create(page=page,defaults={'current_version':version,'revision':1})
        return '/api/v1/app/home' if kind=='HOME' else f'/api/v1/app/pages/{page.id}',deepcopy(config)

    def unlisted_product(self,store):
        from catalog.models import Product,Sku
        from stores.models import StoreProduct
        product=Product.objects.create(product_no='UNLISTED',name='未在餐饮店上架',category=self.product.category,main_image=self.product.main_image,status='ON_SALE',ever_on_sale=True,fulfillment_kind='SHIP')
        Sku.objects.create(product=product,sku_code='UNLISTED',spec_key='default',list_price_fen=1,sale_status='ON_SALE')
        StoreProduct.objects.create(store=store,product=product,on_sale=True)
        return product

    def test_member_and_session_boundary_on_all_public_endpoints(self):
        _,_,_,identity=self.scene()
        paths=['/api/v1/app/products',f'/api/v1/app/products/{self.product.id}',self.publish('HOME')[0],self.publish('MICRO')[0]]
        for path in paths:
            self.assertEqual(self.get(path,identity,token=self.other_token).status_code,403,path)
            self.assertEqual(self.client.get(path,{'diningSessionId':identity}).status_code,401,path)
            self.assertEqual(self.get(path,'not-a-uuid').status_code,400,path)
            self.assertEqual(self.get(path,identity,storeId='').status_code,400,path)
            self.assertEqual(self.get(path,[identity,identity]).status_code,400,path)
        DiningSession.objects.filter(pk=identity).update(expires_at=timezone.now()-timedelta(seconds=1))
        for path in paths:self.assertEqual(self.get(path,identity).status_code,403,path)

    def test_scoped_success_and_errors_are_private_uncacheable(self):
        _,_,_,identity=self.scene()
        paths=['/api/v1/app/products',f'/api/v1/app/products/{self.product.id}',self.publish('HOME')[0],self.publish('MICRO')[0]]
        for path in paths:
            for result in [self.get(path,identity),self.get(path,'invalid'),self.get(path,identity,token=self.other_token),self.client.get(path,{'diningSessionId':identity})]:
                self.assertEqual(result['Cache-Control'],'private, no-store',path)
                self.assertIn('authorization',result.get('Vary','').lower().split(', '),path)
            ordinary=self.client.get(path)
            self.assertNotIn('no-store',ordinary.get('Cache-Control',''),path)

    def test_fixed_warehouse_stock_does_not_fall_back(self):
        store,_,_,identity=self.scene(stock=0)
        self.make_store('OTHER',stock=20)
        result=self.get('/api/v1/app/products',identity)
        self.assertEqual(result.status_code,200,result.content)
        self.assertFalse(result.json()['data']['rows'][0]['cartEligible'])
        result=self.get(f'/api/v1/app/products/{self.product.id}',identity)
        self.assertEqual(result.status_code,200,result.content)
        self.assertFalse(result.json()['data']['skus'][0]['cartEligible'])
        self.assertEqual(result.json()['data']['skus'][0]['availableQuantity'],0)
        store.revision+=1;store.save()
        self.assertEqual(self.get('/api/v1/app/products',identity).status_code,409)

    def test_restaurant_and_warehouse_listing_intersection(self):
        from stores.models import StoreProduct
        store,restaurant,_,identity=self.scene()
        for model,query in [(RestaurantProduct,{'restaurant':restaurant}),(StoreProduct,{'store':store})]:
            model.objects.filter(**query).update(on_sale=False)
            result=self.get('/api/v1/app/products',identity)
            self.assertEqual(result.json()['data']['total'],0)
            self.assertEqual(self.get(f'/api/v1/app/products/{self.product.id}',identity).status_code,404)
            model.objects.filter(**query).update(on_sale=True)

    def test_home_and_micro_components_filter_before_limit_without_config_write(self):
        _,restaurant,_,identity=self.scene(stock=0)
        for kind in ['HOME','MICRO']:
            path,config=self.publish(kind)
            data=self.get(path,identity).json()['data']
            self.assertFalse(data['componentData']['goods'][0]['purchasable'])
            RestaurantProduct.objects.filter(restaurant=restaurant).update(on_sale=False)
            data=self.get(path,identity).json()['data']
            self.assertEqual(data['componentData']['goods'],[])
            self.assertEqual(data['config'],config)
            RestaurantProduct.objects.filter(restaurant=restaurant).update(on_sale=True)

    def test_component_manual_and_category_filter_before_limit(self):
        from pages.runtime import component_data
        from dining_alliance.browsing import resolve_browsing_context
        from django.test import RequestFactory
        store,_,_,identity=self.scene()
        unlisted=self.unlisted_product(store)
        request=RequestFactory().get('/',{'diningSessionId':identity},HTTP_AUTHORIZATION=f'Bearer {self.token}')
        context=resolve_browsing_context(request)
        for source in ['MANUAL','CATEGORY']:
            config={'components':[{'componentId':'goods','type':'PRODUCT_LIST','visible':True,'props':{'source':source,'productIds':[str(unlisted.id),str(self.product.id)],'categoryId':str(self.product.category_id),'sort':'PRICE_ASC','limit':1}}]}
            self.assertEqual([row['productId'] for row in component_data(config,dining_context=context)['goods']],[str(self.product.id)])
        data=self.get('/api/v1/app/products',identity,pageSize=1).json()['data']
        self.assertEqual(data['total'],1)
        self.assertEqual(data['rows'][0]['productId'],str(self.product.id))
        self.assertEqual(self.get(f'/api/v1/app/products/{unlisted.id}',identity).status_code,404)
