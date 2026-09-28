"""Read-only startup media facts for the catalog asset delivery boundary."""

from django.db.models import F, Q

from .models import StartupConfig, StartupConfigVersion


def asset_is_available_to_startup_reader(asset_id):
    draft = StartupConfig.objects.filter(Q(gif_asset_id=asset_id) |
                                         Q(fallback_asset_id=asset_id)).exists()
    current = StartupConfigVersion.objects.filter(
        Q(gif_asset_id=asset_id) | Q(fallback_asset_id=asset_id),
    ).exists()
    return draft or current


def asset_is_in_current_startup_publication(asset_id):
    return StartupConfigVersion.objects.filter(
        Q(gif_asset_id=asset_id) | Q(fallback_asset_id=asset_id),
        startuppublication__current_version_id=F("id"),
    ).exists()
