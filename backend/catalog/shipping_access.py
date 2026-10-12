"""Catalog-owned bounded target read for shipping configuration."""
from django.db.models import Q
from .models import Product, Sku, SkuSpecSelection


def shipping_targets(query,page,size,weights):
    products=Product.objects.filter(Q(name__icontains=query)|Q(skus__sku_code__icontains=query)).distinct().order_by('name','id') if query else Product.objects.order_by('name','id')
    selected=list(products[(page-1)*size:page*size]);product_ids=[row.pk for row in selected]
    skus=list(Sku.objects.filter(product_id__in=product_ids).select_related('current_unit').order_by('sku_code','id'))
    specs={row.pk:[] for row in skus}
    for selection in SkuSpecSelection.objects.filter(sku_id__in=specs).select_related('axis','option').order_by('axis__sort_order'):
        specs[selection.sku_id].append(selection.axis.name+'：'+selection.option.value)
    configured={row['skuId']:row for row in weights}
    items=[]
    for product in selected:
        rows=[]
        for sku in skus:
            if sku.product_id!=product.pk:continue
            weight=configured.get(str(sku.pk),{})
            valid=bool(weight and str(sku.current_unit_id)==weight.get('unitVersionId'))
            rows.append(dict(skuId=str(sku.pk),skuCode=sku.sku_code,specLabel=' / '.join(specs[sku.pk]) or '默认规格',
                saleUnit=sku.current_unit.sale_unit if sku.current_unit else None,unitVersionId=str(sku.current_unit_id) if sku.current_unit_id else None,
                weightGrams=weight.get('weightGrams'),weightUnitVersionId=weight.get('unitVersionId'),weightConfigured=valid))
        items.append(dict(productId=str(product.pk),productName=product.name,skus=rows))
    return dict(items=items,total=products.count(),page=page,pageSize=size)


def shipping_unit_versions(identifiers):
    return {str(row.pk):str(row.current_unit_id) for row in Sku.objects.filter(pk__in=identifiers)}
