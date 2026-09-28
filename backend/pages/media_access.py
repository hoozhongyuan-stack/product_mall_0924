"""Read-only page asset references used by the catalog delivery boundary."""

from django.db.models import F

from .models import PageDraftAsset, PageVersionAsset


def asset_is_available_to_page_reader(asset_id):
    return PageDraftAsset.objects.filter(asset_id=asset_id).exists() or PageVersionAsset.objects.filter(
        asset_id=asset_id,
    ).exists()


def asset_is_in_current_visible_publication(asset_id):
    return PageVersionAsset.objects.filter(
        asset_id=asset_id, is_public=True,
        version__page__pagepublication__current_version_id=F("version_id"),
    ).exists()
