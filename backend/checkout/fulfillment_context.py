"""Separate the storefront source from inventory and operational ownership."""
from inventory.models import Warehouse


def normalize_context(store, mode, snapshot, kinds, *, warehouse=None):
    physical = 'SHIP' in kinds
    if physical and mode in ('PICKUP', 'DELIVERY'):
        return store, mode, snapshot
    warehouse = warehouse or Warehouse.objects.filter(enabled=True, is_default=True).first()
    source = snapshot if store else snapshot.get('sourceStore')
    result = {'fulfillmentWarehouse': {'id': str(warehouse.id), 'name': warehouse.name}} if warehouse else {}
    if source:
        result['sourceStore'] = source
    return None, 'EXPRESS' if physical else 'REDEEM', result


def context_request(quote):
    if quote.store_snapshot.get('dining'):
        return {'diningSessionId': quote.store_snapshot['dining']['sessionId']}
    source = quote.store_snapshot.get('sourceStore')
    store_id = str(quote.store_id) if quote.store_id else source.get('id') if source else None
    result = {'deliveryMode': quote.delivery_mode}
    if store_id:
        result['storeId'] = store_id
    return result
