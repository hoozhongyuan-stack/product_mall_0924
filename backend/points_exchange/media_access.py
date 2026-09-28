"""Public media visibility for active, structurally valid points offers."""
from catalog.exchange_access import eligible_exchange_sku_ids
from .models import ExchangeOffer


def asset_visible_for_exchange(asset_id):
    return ExchangeOffer.objects.filter(status='ON_SALE',sku_id__in=eligible_exchange_sku_ids(),
                                        sku__product__main_image_id=asset_id).exists()
