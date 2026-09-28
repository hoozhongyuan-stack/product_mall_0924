"""Authorization for adding private materials to a business-owned draft."""
from accounts.security import permissions
from .validation import CatalogError


def authorize_asset_binding(actor, identifiers, retained=()):
    from .media import orphan_assets
    added = set(map(str, identifiers)) - set(map(str, retained))
    if not added:
        return
    granted = set(permissions(actor))
    if "asset.read" in granted:
        return
    allowed = set()
    if "asset.upload" in granted:
        allowed = set(map(str, orphan_assets().filter(
            id__in=added, created_by=actor).values_list("id", flat=True)))
    if added - allowed:
        raise CatalogError("当前账号没有所选素材的使用权限。", "PERMISSION_DENIED", 403)
