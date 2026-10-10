"""Bounded read-only public product cards for page decoration."""
from django.db.models import OuterRef, Subquery, Q, Case, When, IntegerField
from django.utils import timezone
from inventory.availability import product_has_available_stock
from payments.availability import enabled_payment_methods
from .models import Product, Sku


def hydrate_products(props):
    if (props['source'] == 'MANUAL' and not props['productIds']) or (props['source'] == 'CATEGORY' and not props['categoryId']):
        return []
    cheapest = Sku.objects.filter(product_id=OuterRef('pk'), sale_status=Sku.SaleStatus.ON_SALE).order_by('list_price_fen', 'id')
    query = Product.objects.filter(status=Product.Status.ON_SALE, main_image__isnull=False,
        category__status='ACTIVE', category__parent__status='ACTIVE').annotate(
            price_fen=Subquery(cheapest.values('list_price_fen')[:1]), has_stock=product_has_available_stock()).filter(price_fen__isnull=False)
    if props['source'] == 'MANUAL':
        query = query.filter(id__in=props['productIds']).order_by(Case(
            *[When(id=identifier, then=index) for index, identifier in enumerate(props['productIds'])],
            output_field=IntegerField()))
    else:
        query = query.filter(Q(category_id=props['categoryId']) | Q(category__parent_id=props['categoryId']))
        query = query.order_by('price_fen', 'id') if props['sort'] == 'PRICE_ASC' else query.order_by('-created_at', 'id')
    payment_available = bool(enabled_payment_methods())
    return [{'productId': str(item.id), 'name': item.name, 'priceFen': item.price_fen,
             'imageUrl': f'/api/v1/app/assets/{item.main_image_id}/file',
             'purchasable': bool(item.has_stock and payment_available and (
                 item.fulfillment_kind != Product.Fulfillment.REDEEM or
                 (item.redeem_valid_until and item.redeem_valid_until >= timezone.localdate())))}
            for item in query[:props['limit']]]
