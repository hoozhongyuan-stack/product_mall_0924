"""Read-only product-level profit policy boundary used by order snapshot creation."""
from .models import StoreProductProfitRule, StoreSkuProfitRule


def effective_share(product_id, legacy=None, explicit=None):
    row = explicit if explicit is not None else (StoreProductProfitRule.objects.filter(pk=product_id).first() if legacy is None else None)
    if row:
        return row.platform_share_bps, row.revision, 'PRODUCT'
    legacy = list(legacy) if legacy is not None else list(StoreSkuProfitRule.objects.filter(sku__product_id=product_id))
    ratios = {rule.platform_share_bps for rule in legacy if not rule.costs_only}
    if len(ratios) == 1:
        return next(iter(ratios)), 0, 'LEGACY_UNIFORM'
    return None, 0, 'LEGACY_CONFLICT' if ratios else 'UNCONFIGURED'
