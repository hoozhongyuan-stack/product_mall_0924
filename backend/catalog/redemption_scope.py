"""Catalog-owned redemption policy; order lines freeze this data at purchase."""
from uuid import UUID


def parse_scope(fulfillment, scope='PLATFORM', store_ids=None):
    from .validation import CatalogError
    from stores.models import Store
    if scope not in ('PLATFORM', 'STORE', 'BOTH'):
        raise CatalogError('请选择正确的核销范围。')
    ids = [] if store_ids is None else store_ids
    if not isinstance(ids, list) or len(ids) > 100:
        raise CatalogError('核销前置仓列表格式不正确。')
    try:
        normalized = sorted(str(UUID(value)) for value in ids if isinstance(value, str))
    except ValueError as exc:
        raise CatalogError('核销前置仓编号格式不正确。') from exc
    if len(normalized) != len(ids) or len(set(normalized)) != len(ids):
        raise CatalogError('核销前置仓编号不可重复或缺失。')
    if fulfillment != 'REDEEM':
        if scope != 'PLATFORM' or ids:
            raise CatalogError('实物商品不配置虚拟核销范围。')
        return 'PLATFORM', []
    if scope == 'PLATFORM' and ids or scope in ('STORE', 'BOTH') and not ids:
        raise CatalogError('指定前置仓核销须至少选择一个前置仓；仅平台核销不能指定前置仓。')
    stores = Store.objects.filter(pk__in=normalized, enabled=True, warehouse__enabled=True)
    if stores.count() != len(normalized):
        raise CatalogError('指定核销前置仓不存在或已停用。')
    return scope, normalized


def scope_snapshot(product):
    from stores.models import Store
    stores = Store.objects.filter(pk__in=product.redeem_store_ids).order_by('id')
    return {'scope': product.redeem_scope, 'stores': [
        {'storeId': str(store.id), 'name': store.name} for store in stores]}


def scope_data(snapshot):
    snapshot = snapshot or {'scope': 'PLATFORM', 'stores': []}
    stores = snapshot.get('stores', [])
    return {'redeemScope': snapshot.get('scope', 'PLATFORM'),
            'redeemStoreIds': [row['storeId'] for row in stores], 'redeemStores': stores}


def product_scope_data(product):
    return scope_data(scope_snapshot(product))


def redemption_stores_view(request):
    from accounts.security import require, response
    from common.http import method
    from stores.models import Store
    bad = method(request, 'GET')
    if bad:
        return bad
    _, bad = require(request, 'catalog.read')
    if bad:
        return bad
    stores = Store.objects.filter(enabled=True, warehouse__enabled=True).order_by('name', 'id')
    return response(request, {'items': [{'storeId': str(store.id), 'name': store.name} for store in stores]})
