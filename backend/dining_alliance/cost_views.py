"""Retired restaurant-specific costs remain readable as historical archives."""
from django.db.models import Q
from accounts.security import require
from common.http import method,offset_response
from stores.access import get_store
from .models import StoreSkuPurchaseCost
from .access import DiningError
from .views import guarded,page_data


def cost_data(row):
    return {'storeId':str(row.store_id),'skuId':str(row.sku_id),'skuCode':row.sku.sku_code,'productName':row.sku.product.name,'storeName':row.store.name,'purchaseCostFen':row.purchase_cost_fen,'unitVersionId':str(row.unit_version_id),'revision':row.revision,'source':'STORE_OVERRIDE','archived':True}


@guarded
def costs_view(request):
    bad=method(request,'GET','PUT','PATCH')
    if bad:return bad
    _,bad=require(request,'dining.read' if request.method=='GET' else 'dining.manage')
    if bad:return bad
    if request.method!='GET':
        raise DiningError('餐饮独立进货价已停用，请在前置仓进货价配置中维护；历史记录保留。','DINING_COST_RETIRED',410)
    rows=StoreSkuPurchaseCost.objects.select_related('store','sku__product').order_by('store_id','sku_id')
    if request.GET.get('storeId'):
        rows=rows.filter(store=get_store(request.GET['storeId']))
    query=request.GET.get('search','').strip()[:120]
    if query:rows=rows.filter(Q(sku__product__name__icontains=query)|Q(sku__sku_code__icontains=query)|Q(store__name__icontains=query))
    return offset_response(request,{**page_data(request,rows,cost_data),'retired':True,'activeCostConfiguration':'/api/v1/admin/stores/purchase-costs'})
