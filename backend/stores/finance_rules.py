"""Admin SKU purchase-price and platform profit-share configuration."""
import hashlib
import json
import uuid
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


def valid_amounts(body):
    return (type(body.get('purchaseCostFen')) is int and
            0 <= body['purchaseCostFen'] <= 9900000000 and
            type(body.get('platformShareBps')) is int and
            0 <= body['platformShareBps'] <= 10000)


def batch_items(body):
    if (set(body) != {'items', 'purchaseCostFen', 'platformShareBps'} or
            not valid_amounts(body) or not isinstance(body['items'], list) or
            not 1 <= len(body['items']) <= 100):
        raise StoreError('请选择 1 至 100 个规格并填写有效的进货价和平台分成比例。')
    items = []
    for item in body['items']:
        if (not isinstance(item, dict) or set(item) != {'skuId', 'expectedRevision'} or
                not isinstance(item['skuId'], str) or
                type(item['expectedRevision']) is not int or item['expectedRevision'] < 0):
            raise StoreError('商品规格或修订号无效。')
        items.append((str(uuid.UUID(item['skuId'])), item['expectedRevision']))
    if len({sku_id for sku_id, _ in items}) != len(items):
        raise StoreError('商品规格不能重复选择。')
    return sorted(items)


def batch_confirmation_target(body, items):
    """ASCII canonical payload shared with the PC confirmation helper."""
    payload = [body['purchaseCostFen'], body['platformShareBps'], items]
    return hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()


def save_rule(request, actor, sku, row, body, action='stores.profit.configure'):
    before = rule_data(sku, row)
    updated = StoreSkuProfitRule(sku=sku, unit_version=sku.current_unit,
        purchase_cost_fen=body['purchaseCostFen'], platform_share_bps=body['platformShareBps'],
        revision=(row.revision if row else 0) + 1)
    updated.save()
    after = rule_data(sku, updated)
    audit(request, action, 'store_sku_profit_rule', sku.pk, actor, before=before, after=after)
    return after


def configure_batch(request, actor):
    body = parse_json_object(request, max_bytes=16384)
    items = batch_items(body)
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor, bad = require_live(request, 'stores.manage')
        if bad:
            return bad
        skus = list(Sku.objects.select_for_update(of=('self',)).filter(
            pk__in=[sku_id for sku_id, _ in items], product__fulfillment_kind='SHIP')
            .select_related('product', 'current_unit').prefetch_related(specification_prefetch()).order_by('id'))
        if len(skus) != len(items):
            raise StoreError('商品规格不存在。', 'SKU_NOT_FOUND', 404)
        rules = {str(row.sku_id): row for row in StoreSkuProfitRule.objects.select_for_update()
                 .filter(sku_id__in=[sku.pk for sku in skus]).order_by('sku_id')}
        expected = dict(items)
        for sku in skus:
            row = rules.get(str(sku.pk))
            if (row.revision if row else 0) != expected[str(sku.pk)]:
                return error(request, 409, 'REVISION_CONFLICT', '部分规则已修改，请刷新后重新选择。')
            if not sku.current_unit_id:
                raise StoreError('所选规格尚未全部配置销售单位，请配置后重试。')
        bad = confirm_action(request, actor, 'stores.profit.batch.configure',
                             batch_confirmation_target(body, items), 0)
        if bad:
            return bad
        result = [save_rule(request, actor, sku, rules.get(str(sku.pk)), body,
                            'stores.profit.batch.configure') for sku in skus]
    return response(request, {'items': result})


@cache_control(private=True,no_store=True)
@guarded
def profit_rules(request,sku_id=None):
    bad=method(request,*(['PUT'] if sku_id else ['GET','POST']))
    if bad:return bad
    actor,bad=require(request,'stores.read' if request.method=='GET' else 'stores.manage')
    if bad:return bad
    if request.method=='POST':return configure_batch(request,actor)
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
        after=save_rule(request,actor,sku,row,body)
    return response(request,after)
