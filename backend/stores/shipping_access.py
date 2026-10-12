"""Minimal store identity/location read for authorized shipping administrators."""
from .models import Store


def shipping_store_rows(query='',page=1,size=20,identifier=None):
    rows=Store.objects.order_by('name','id')
    if identifier:rows=rows.filter(pk=identifier)
    elif query:rows=rows.filter(name__icontains=query)
    total=rows.count()
    return {'items':[dict(id=str(row.pk),name=row.name,city=row.city,
        latitude=float(row.latitude) if row.latitude is not None else None,
        longitude=float(row.longitude) if row.longitude is not None else None) for row in rows[(page-1)*size:page*size]],
        'total':total,'page':page,'pageSize':size}
