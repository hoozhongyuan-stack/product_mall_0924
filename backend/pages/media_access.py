"""Read-only page asset references used by the catalog delivery boundary."""

from django.db.models import F, Q

from .models import PageDraftAsset, PageVersionAsset, StorefrontDraftAsset, StorefrontVersionAsset


def asset_is_available_to_page_reader(asset_id):
    return PageDraftAsset.objects.filter(asset_id=asset_id).exists() or PageVersionAsset.objects.filter(
        asset_id=asset_id,
    ).exists()


def asset_is_in_current_visible_publication(asset_id):
    return PageVersionAsset.objects.filter(
        asset_id=asset_id, is_public=True,
        version__page__pagepublication__current_version_id=F("version_id"),
    ).exists()


def storefront_readable_domains(asset_id):
    draft = StorefrontDraftAsset.objects.filter(asset_id=asset_id).values_list("config_id", flat=True)
    history = StorefrontVersionAsset.objects.filter(asset_id=asset_id).values_list(
        "version__config_id", flat=True)
    return set(draft).union(history)


def asset_is_in_current_storefront_publication(asset_id):
    return StorefrontVersionAsset.objects.filter(
        asset_id=asset_id,
        version__config__storefrontpublication__current_version_id=F("version_id"),
    ).filter(Q(version__config_id="navigation") |
             Q(version__config_id="customer_service", version__config_json__enabled=True),
    ).exists()
