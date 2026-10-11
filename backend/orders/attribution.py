"""Read ownership from immutable checkout snapshots, never current restaurant data."""
from uuid import UUID


def filter_attribution(rows, params):
    from payments.service import PaymentError
    source = params.get('source', '')
    if source not in ('', 'DINING', 'REGULAR'):
        raise PaymentError('订单来源筛选参数不正确。')
    if source == 'DINING':
        rows = rows.filter(store_snapshot__has_key='dining')
    elif source == 'REGULAR':
        rows = rows.exclude(store_snapshot__has_key='dining')
    restaurant_id = params.get('restaurantId')
    if restaurant_id:
        try:
            restaurant_id = str(UUID(restaurant_id))
        except (ValueError, TypeError, AttributeError):
            raise PaymentError('餐饮店编号不正确。')
        rows = rows.filter(store_snapshot__dining__restaurantId=restaurant_id)
    return rows


def attribution_data(order):
    snapshot = order.store_snapshot
    return {'source': 'DINING' if snapshot.get('dining') else 'REGULAR',
            'fulfillmentWarehouse': snapshot.get('fulfillmentWarehouse')}
