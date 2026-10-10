"""Admin SKU purchase-price and platform profit-share configuration."""
from django.db import transaction
from django.views.decorators.cache import cache_control
from accounts.models import AdminAccount
from accounts.security import require,require_live,confirm_action,audit
from catalog.models import Sku
from catalog.spec_read import specification_label,specification_prefetch
from common.http import response,error,method,parse_json_object
from .access import StoreError
from .models import StoreSkuProfitRule
from .views import guarded,page_bounds


def rule_data(sku,rule=None):
    return {'skuId':str(sku.id),'productName':sku.product.name,'specKey':sku.spec_key,'specLabel':specification_label(sku),'skuCode':sku.sku_code,'saleUnit':sku.current_unit.sale_unit if sku.current_unit else '', 'purchaseCostFen':rule.purchase_cost_fen if rule else None,'platformShareBps':rule.platform_share_bps if rule else None,'revision':rule.revision if rule else 0,'requiresReconfiguration':bool(rule and rule.unit_version_id!=sku.current_unit_id)}


@cache_control(private=True,no_store=True)
@guarded
def profit_rules(request,sku_id=None):
    bad=method(request,*(['PUT'] if sku_id else ['GET']))
    if bad:return bad
    actor,bad=require(request,'stores.manage' if sku_id else 'stores.read')
    if bad:return bad
    if not sku_id:
        from django.db.models import Q
        rows=Sku.objects.filter(product__fulfillment_kind='SHIP').select_related('product','current_unit').prefetch_related(specification_prefetch()).order_by('sku_code','id')
        search=request.GET.get('search','').strip()[:120]
        if search:rows=rows.filter(Q(product__name__icontains=search)|Q(sku_code__icontains=search))
        page,size,start=page_bounds(request)
        skus=list(rows[start:start+size]);rules={row.sku_id:row for row in StoreSkuProfitRule.objects.filter(sku_id__in=[sku.id for sku in skus])}
        return response(request,{'items':[rule_data(sku,rules.get(sku.id)) for sku in skus],'total':rows.count(),'page':page,'pageSize':size})
    body=parse_json_object(request,max_bytes=2048)
    if set(body)!={'expectedRevision','purchaseCostFen','platformShareBps'} or any(type(body[key]) is not int for key in body) or not 0<=body['expectedRevision'] or not 0<=body['purchaseCostFen']<=9900000000 or not 0<=body['platformShareBps']<=10000:
        raise StoreError('进货价或平台分成比例无效。')
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor,bad=require_live(request,'stores.manage')
        if bad:return bad
        sku=Sku.objects.select_for_update(of=('self',)).select_related('product','current_unit').prefetch_related(specification_prefetch()).filter(pk=sku_id,product__fulfillment_kind='SHIP').first()
        if not sku:raise StoreError('商品规格不存在。','SKU_NOT_FOUND',404)
        if not sku.current_unit_id:raise StoreError('该规格尚未配置销售单位。')
        row=StoreSkuProfitRule.objects.select_for_update().filter(pk=sku_id).first()
        revision=row.revision if row else 0
        if revision!=body['expectedRevision']:return error(request,409,'REVISION_CONFLICT','规则已修改，请刷新后重试。')
        bad=confirm_action(request,actor,'stores.profit.configure',str(sku_id),revision)
        if bad:return bad
        before=rule_data(sku,row)
        row=StoreSkuProfitRule(sku=sku,unit_version=sku.current_unit,purchase_cost_fen=body['purchaseCostFen'],platform_share_bps=body['platformShareBps'],revision=revision+1)
        row.save()
        after=rule_data(sku,row)
        audit(request,'stores.profit.configure','store_sku_profit_rule',sku_id,actor,before=before,after=after)
    return response(request,after)
