"""Independent product share and SKU cost admin operations with optimistic revisions."""
import hashlib
import json
import uuid
from django.db import transaction
from django.db.models import Prefetch, Q, Count
from django.views.decorators.cache import cache_control
from accounts.models import AdminAccount
from accounts.security import require, require_live, confirm_action, audit
from catalog.models import Product, Sku
from catalog.spec_read import specification_prefetch
from common.http import response, error, method, parse_json_object
from .access import StoreError
from .finance_rules import rule_data
from .models import StoreProductProfitRule, StoreSkuProfitRule
from .views import guarded, page_bounds


from .profit_access import effective_share


def product_data(product, rules, shares):
    skus = list(product.skus.all())
    share, revision, source = effective_share(product.pk, [rules[sku.pk] for sku in skus if sku.pk in rules], shares.get(product.pk))
    return {'productId': str(product.pk), 'productName': product.name, 'productNo': product.product_no,
            'categoryId': str(product.category_id), 'platformShareBps': share, 'shareRevision': revision,
            'shareSource': source, 'skus': [rule_data(sku, rules.get(sku.pk)) for sku in skus]}


@cache_control(private=True, no_store=True)
@guarded
def product_rules(request):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, bad = require(request, 'stores.read')
    if bad:
        return bad
    page, size, start = page_bounds(request)
    search = request.GET.get('search', '').strip()
    if len(search) > 120:
        raise StoreError('检索内容过长。')
    rows = Product.objects.filter(fulfillment_kind='SHIP').annotate(legacy_count=Count('skus__storeskuprofitrule', filter=Q(skus__storeskuprofitrule__costs_only=False))).order_by('product_no', 'id')
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(product_no__icontains=search) | Q(skus__sku_code__icontains=search)).distinct()
    category = request.GET.get('categoryId')
    if category:
        category = uuid.UUID(category)
        rows = rows.filter(Q(category_id=category) | Q(category__parent_id=category))
    configuration = request.GET.get('configuration', 'ALL')
    if configuration not in {'ALL', 'CONFIGURED', 'UNCONFIGURED', 'NEEDS_CONFIRMATION'}:
        raise StoreError('配置状态不正确。')
    # Database filtering remains paginated; legacy differing ratios need explicit confirmation.
    if configuration == 'CONFIGURED':
        rows = rows.filter(storeproductprofitrule__isnull=False)
    elif configuration == 'UNCONFIGURED':
        rows = rows.filter(storeproductprofitrule__isnull=True, legacy_count=0)
    elif configuration == 'NEEDS_CONFIRMATION':
        rows = rows.filter(storeproductprofitrule__isnull=True, legacy_count__gt=0)
    total = rows.count()
    sku_query = Sku.objects.select_related('product', 'current_unit').prefetch_related(specification_prefetch()).order_by('sku_code', 'id')
    products = list(rows.prefetch_related(Prefetch('skus', queryset=sku_query))[start:start+size])
    rules = {row.sku_id: row for row in StoreSkuProfitRule.objects.filter(sku__product_id__in=[p.pk for p in products])}
    shares = {row.product_id: row for row in StoreProductProfitRule.objects.filter(pk__in=[p.pk for p in products])}
    return response(request, {'items': [product_data(p, rules, shares) for p in products], 'total': total, 'page': page, 'pageSize': size})


def parse_items(body, share):
    if set(body) != ({'items', 'platformShareBps'} if share else {'items'}):
        raise StoreError('请求字段不正确。')
    if share and (type(body.get('platformShareBps')) is not int or not 0 <= body['platformShareBps'] <= 10000):
        raise StoreError('平台比例须在 0 至 100% 之间。')
    items = body.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise StoreError('请选择 1 至 100 项。')
    normalized = []
    identity = 'productId' if share else 'skuId'
    for item in items:
        fields = {identity, 'expectedRevision'} | (set() if share else {'purchaseCostFen', 'expectedUnitVersionId'})
        if not isinstance(item, dict) or set(item) != fields or type(item.get('expectedRevision')) is not int or item['expectedRevision'] < 0:
            raise StoreError('商品或修订号无效。')
        if not isinstance(item.get(identity), str):
            raise StoreError('商品 ID 无效。')
        identifier = str(uuid.UUID(item[identity]))
        if not share and (type(item['purchaseCostFen']) is not int or not 0 <= item['purchaseCostFen'] <= 9900000000):
            raise StoreError('进货价无效。')
        unit_id = None
        if not share and item['expectedUnitVersionId'] is not None:
            if not isinstance(item['expectedUnitVersionId'], str):
                raise StoreError('销售单位版本无效。')
            unit_id = str(uuid.UUID(item['expectedUnitVersionId']))
        normalized.append([identifier, item['expectedRevision']] + ([] if share else [item['purchaseCostFen'], unit_id]))
    if len({row[0] for row in normalized}) != len(normalized):
        raise StoreError('不能重复选择。')
    return sorted(normalized)


@cache_control(private=True, no_store=True)
@guarded
def configure(request, kind):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = require(request, 'stores.manage')
    if bad:
        return bad
    share = kind == 'share'
    body = parse_json_object(request, max_bytes=32768)
    items = parse_items(body, share)
    payload = [body['platformShareBps'], items] if share else items
    target = hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
    action = 'stores.share.batch.configure' if share else 'stores.cost.batch.configure'
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor, bad = require_live(request, 'stores.manage')
        if bad:
            return bad
        model = Product if share else Sku
        query = model.objects.select_for_update(of=('self',)).filter(pk__in=[row[0] for row in items])
        query = query.filter(fulfillment_kind='SHIP') if share else query.filter(product__fulfillment_kind='SHIP').select_related('current_unit', 'product')
        entities = {str(row.pk): row for row in query.order_by('id')}
        if len(entities) != len(items):
            raise StoreError('商品或规格不存在。', 'NOT_FOUND', 404)
        rule_model = StoreProductProfitRule if share else StoreSkuProfitRule
        rules = {str(row.pk): row for row in rule_model.objects.select_for_update().filter(pk__in=entities).order_by('pk')}
        for identifier, revision, *cost in items:
            row = rules.get(identifier)
            if (row.revision if row else 0) != revision:
                return error(request, 409, 'REVISION_CONFLICT', '部分配置已修改，请刷新后重试。')
            if not share and str(entities[identifier].current_unit_id or '') != (cost[1] or ''):
                return error(request, 409, 'UNIT_REVISION_CONFLICT', '规格销售单位已修改，请刷新后重新填写进货价。')
            if not share and not entities[identifier].current_unit_id:
                raise StoreError('规格尚未配置销售单位。')
        bad = confirm_action(request, actor, action, target, 0)
        if bad:
            return bad
        result = []
        for identifier, revision, *cost in items:
            previous = rules.get(identifier)
            if share:
                values = {'product_id': identifier, 'platform_share_bps': body['platformShareBps']}
            else:
                values = {'sku_id': identifier, 'unit_version_id': entities[identifier].current_unit_id,
                          'purchase_cost_fen': cost[0], 'platform_share_bps': previous.platform_share_bps if previous else 0,
                          'costs_only': previous.costs_only if previous else True}
            updated = rule_model(**values, revision=revision+1)
            updated.save()
            after = {'productId': identifier, 'platformShareBps': updated.platform_share_bps, 'shareRevision': updated.revision} if share else rule_data(entities[identifier], updated)
            audit(request, action, 'store_product_profit_rule' if share else 'store_sku_profit_rule', identifier, actor,
                  before={'revision': revision, 'platformShareBps': previous.platform_share_bps if previous else None, **({} if share else {'purchaseCostFen': previous.purchase_cost_fen if previous else None})}, after=after)
            result.append(after)
    return response(request, {'items': result})
