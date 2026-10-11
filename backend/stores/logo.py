"""Stable warehouse logos reuse validated, protected material assets."""
from catalog.asset_access import authorize_asset_binding
from catalog.media import lock_available_assets
from catalog.models import Asset
from catalog.validation import CatalogError, uuid_field
from .access import StoreError


def resolve_logo(identifier, actor, retained=None):
    if identifier is None:
        return None
    try:
        identifier = uuid_field(identifier, '前置仓 Logo')
        if actor is None:
            raise CatalogError('当前账号没有素材使用权限。', 'PERMISSION_DENIED', 403)
        authorize_asset_binding(actor, [identifier], [retained] if retained else [])
        asset = lock_available_assets([identifier])[identifier]
        if (asset.kind != Asset.Kind.IMAGE or asset.content_type not in ('image/png', 'image/jpeg') or
                not asset.width or asset.width != asset.height or asset.byte_size > 5 * 1024 * 1024):
            raise CatalogError('Logo 须为不超过 5MB 的正方形 PNG 或 JPEG 图片。', 'MEDIA_INVALID')
        return asset
    except CatalogError as exc:
        raise StoreError(str(exc), exc.code, exc.status) from exc
