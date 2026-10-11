"""Depth-specific category images retain ordinary material authorization/storage rules."""
from .asset_access import authorize_asset_binding
from .media import lock_available_assets
from .models import Asset
from .validation import CatalogError, uuid_field


def category_media(actor, values, parent, current=None):
    fields = {'imageAssetId': 'image', 'bannerAssetId': 'banner'}
    identifiers = {}
    for key, field in fields.items():
        if key not in values:
            continue
        raw = values[key]
        if raw is not None and ((field == 'image' and parent is not None) or (field == 'banner' and parent is None)):
            raise CatalogError('一级分类使用正方形图，二级分类使用横幅图。')
        identifiers[field] = uuid_field(raw, '分类图片') if raw is not None else None
    authorize_asset_binding(actor, [value for value in identifiers.values() if value],
                            [getattr(current, field + '_id') for field in fields.values()] if current else [])
    assets = lock_available_assets([value for value in identifiers.values() if value])
    result = {}
    for field, identifier in identifiers.items():
        asset = assets.get(identifier) if identifier else None
        if asset and (asset.kind != Asset.Kind.IMAGE or asset.content_type not in ('image/png', 'image/jpeg') or asset.byte_size > 5 * 1024 * 1024 or not asset.width or not asset.height):
            raise CatalogError('分类图片须为有效 PNG 或 JPEG 图片。', 'MEDIA_INVALID')
        if asset and ((field == 'image' and asset.width != asset.height) or (field == 'banner' and asset.width <= asset.height)):
            raise CatalogError('一级分类图须为正方形，二级分类横幅宽度须大于高度。', 'MEDIA_INVALID')
        result[field] = asset
    return result
