"""Read-only catalog facts used when validating and publishing page content."""

from django.db.models import Q

from .models import Asset, Category, Product, Sku
from .validation import CatalogError


def category_is_available(category_id):
    return Category.objects.filter(id=category_id, status=Category.Status.ACTIVE).filter(
        Q(parent__isnull=True) | Q(parent__status=Category.Status.ACTIVE)).exists()


def product_is_available(product_id):
    return Product.objects.filter(
        id=product_id, status=Product.Status.ON_SALE,
        main_image__isnull=False, category__status=Category.Status.ACTIVE,
        category__parent__status=Category.Status.ACTIVE,
        skus__sale_status=Sku.SaleStatus.ON_SALE,
    ).exists()


def page_image_is_available(asset_id):
    from .media import available_asset
    asset = Asset.objects.filter(id=asset_id, kind=Asset.Kind.IMAGE).first()
    return bool(asset and available_asset(asset))


def assets_exist(asset_ids):
    # Called within the page-save transaction: material cleanup must wait until
    # the retaining reference is committed, or the save rejects cleanly.
    from .media import lock_available_assets
    try:
        lock_available_assets(asset_ids)
        return True
    except CatalogError as exc:
        if exc.status >= 500:
            raise
        return False
