"""Bounded read-only product validation for coupon scope."""
from .models import Product


def coupon_product_names(product_ids):
    return [{'id':str(row.id), 'name':row.name} for row in
            Product.objects.filter(id__in=product_ids).only('id','name').order_by('id')]


def coupon_products_exist(product_ids):
    return len(product_ids) <= 100 and Product.objects.filter(id__in=product_ids).count() == len(product_ids)


def search_product_options(search,page,page_size):
    from django.db.models import Q
    query=Product.objects.all()
    if search:
        query=query.filter(Q(name__icontains=search)|Q(product_no__icontains=search))
    total=query.count()
    return [{'id':str(row.id),'name':row.name,'productNo':row.product_no,'status':row.status}
            for row in query.only('id','name','product_no','status').order_by('name','id')
            [(page-1)*page_size:page*page_size]],total
