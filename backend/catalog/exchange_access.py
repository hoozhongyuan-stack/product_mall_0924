"""Structural catalog facts for independently priced points offers.

Cash price, grade discounts and cash sale switches are not points pricing rules.
A points offer still requires published product content, image, active category,
valid units and an unexpired fixed redemption date.
"""
from django.db.models import Q
from django.utils import timezone
from .models import Product,Category,Sku,SkuSpecSelection


def exchange_catalog_rows(sku_ids,*,lock=False):
    if lock:
        ids=list(Sku.objects.filter(pk__in=sku_ids).values_list('product_id',flat=True))
        products=list(Product.objects.select_for_update(of=('self',)).select_related('category').filter(pk__in=ids).order_by('id'))
        categories={row.category_id for row in products}
        categories.update(row.category.parent_id for row in products if row.category.parent_id)
        list(Category.objects.select_for_update().filter(pk__in=categories).order_by('id'))
    query=Sku.objects.select_related('product__category__parent','current_unit')
    if lock: query=query.select_for_update(of=('self',))
    skus=list(query.filter(pk__in=sku_ids).order_by('id'))
    specs={sku.id:[] for sku in skus}
    for row in SkuSpecSelection.objects.filter(sku_id__in=sku_ids).select_related('axis','option').order_by('axis__sort_order','axis_id'):
        specs[row.sku_id].append({'name':row.axis.name,'value':row.option.value})
    result={}
    for sku in skus:
        product=sku.product; category=product.category; unit=sku.current_unit
        eligible=bool(product.ever_on_sale and not product.manually_off_sale and product.main_image_id and category.status=='ACTIVE'
            and category.parent and category.parent.status=='ACTIVE' and unit
            and (product.fulfillment_kind!='REDEEM' or product.redeem_valid_until and product.redeem_valid_until>=timezone.localdate()))
        result[sku.id]={'sku':sku,'unit':unit,'eligible':eligible,'skuId':str(sku.id),'productId':str(product.id),
            'name':product.name,'productName':product.name,'skuCode':sku.sku_code,'specs':specs[sku.id],
            'saleUnit':unit.sale_unit if unit else None,'ratio':unit.ratio if unit else None,
            'fulfillmentKind':product.fulfillment_kind,'redeemValidUntil':product.redeem_valid_until.isoformat() if product.redeem_valid_until else None,
            'unitVersionId':str(unit.id) if unit else None,
            'imageUrl':f'/api/v1/app/assets/{product.main_image_id}/file' if product.main_image_id else None}
    return result


def exchange_sku_options(search,page,size):
    query=Sku.objects.select_related('product','current_unit').all()
    if search: query=query.filter(Q(sku_code__icontains=search)|Q(product__name__icontains=search))
    total=query.count(); skus=list(query.order_by('sku_code','id')[(page-1)*size:page*size])
    rows=exchange_catalog_rows([row.id for row in skus])
    return [{'id':str(sku.id),**{key:row[key] for key in ['skuId','productId','productName','skuCode','specs','saleUnit','fulfillmentKind','redeemValidUntil']},
             'catalogOnSale':row['eligible']} for sku in skus for row in [rows[sku.id]]],total


def eligible_exchange_sku_ids():
    return Sku.objects.filter(product__ever_on_sale=True, product__manually_off_sale=False,
        product__main_image__isnull=False,
        current_unit__isnull=False,product__category__status='ACTIVE',product__category__parent__status='ACTIVE').filter(
        Q(product__fulfillment_kind='SHIP') | Q(product__fulfillment_kind='REDEEM',product__redeem_valid_until__gte=timezone.localdate())).values('id')
