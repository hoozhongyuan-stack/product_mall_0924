"""Warehouse-specific purchase costs with explicit global fallback source."""
from django.db import transaction
from catalog.models import Sku
from stores.access import get_store
from accounts.security import require, require_live, audit
from common.http import method, response, parse_json_object, offset_response
from .models import StoreSkuPurchaseCost
from .access import DiningError
from .views import guarded, page_data


def cost_data(row):
    return {'storeId':str(row.store_id),'skuId':str(row.sku_id),'skuCode':row.sku.sku_code,'productName':row.sku.product.name,'storeName':row.store.name,'purchaseCostFen':row.purchase_cost_fen,'unitVersionId':str(row.unit_version_id),'revision':row.revision,'source':'STORE_OVERRIDE'}


@guarded
def costs_view(request):
    bad=method(request,'GET','PUT','PATCH')
    if bad:return bad
    actor,bad=require(request,'dining.read' if request.method=='GET' else 'dining.manage')
    if bad:return bad
    if request.method=='GET':
        rows=StoreSkuPurchaseCost.objects.select_related('store','sku__product').order_by('store_id','sku_id')
        if request.GET.get('storeId'):
            store=get_store(request.GET['storeId'])
            skus=Sku.objects.select_related('product','current_unit').filter(product__fulfillment_kind='SHIP').exclude(product__status='DRAFT').order_by('sku_code','id')
            query=request.GET.get('search','').strip()[:120]
            if query:skus=skus.filter(product__name__icontains=query)
            overrides={row.sku_id:row for row in rows.filter(store=store)}
            from stores.models import StoreSkuProfitRule
            defaults={row.sku_id:row for row in StoreSkuProfitRule.objects.filter(sku__in=skus)}
            def candidate(sku):
                override=overrides.get(sku.pk)
                default=defaults.get(sku.pk)
                valid=default is not None and default.unit_version_id==sku.current_unit_id
                return {'storeId':str(store.id),'storeName':store.name,'skuId':str(sku.pk),'skuCode':sku.sku_code,'productName':sku.product.name,'unitVersionId':str(sku.current_unit_id) if sku.current_unit_id else None,'purchaseCostFen':override.purchase_cost_fen if override else (default.purchase_cost_fen if valid else None),'revision':override.revision if override else 0,'source':'STORE_OVERRIDE' if override else ('GLOBAL_DEFAULT' if valid else 'UNCONFIGURED')}
            return offset_response(request,page_data(request,skus,candidate))
        return offset_response(request,page_data(request,rows,cost_data))
    body=parse_json_object(request)
    if set(body)!={'storeId','skuId','purchaseCostFen','unitVersionId','revision'} or type(body['purchaseCostFen']) is not int or not 0<=body['purchaseCostFen']<=10**12 or type(body['revision']) is not int:
        raise DiningError('采购成本配置无效。')
    with transaction.atomic():
        sku=Sku.objects.select_for_update().select_related('product').filter(pk=body['skuId'],product__fulfillment_kind='SHIP').first()
        if sku is None or str(sku.current_unit_id)!=body['unitVersionId']:
            raise DiningError('商品单位版本已变化，请刷新。','REVISION_CONFLICT',409)
        store=get_store(body['storeId'])
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        row=StoreSkuPurchaseCost.objects.select_for_update().filter(store=store,sku=sku).first()
        if body['revision']!=(row.revision if row else 0):raise DiningError('采购成本已变化，请刷新。','REVISION_CONFLICT',409)
        if row:
            row.unit_version_id=body['unitVersionId'];row.purchase_cost_fen=body['purchaseCostFen'];row.revision+=1;row.save()
        else:row=StoreSkuPurchaseCost.objects.create(store=store,sku=sku,unit_version_id=body['unitVersionId'],purchase_cost_fen=body['purchaseCostFen'])
        data=cost_data(row)
        audit(request,'dining.purchase_cost.save','dining_purchase_cost',row.id,actor=actor,after=data)
    return response(request,data)
