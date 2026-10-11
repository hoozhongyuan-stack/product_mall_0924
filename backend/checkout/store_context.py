"""Store selection and delivery eligibility shared by quote and order submission."""
import math
from uuid import UUID

from customers.models import CustomerAddress


def distance_meters(latitude, longitude, other_latitude, other_longitude):
    a, b = math.radians(float(latitude)), math.radians(float(other_latitude))
    delta = math.radians(float(other_longitude) - float(longitude))
    value = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(delta/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, value)))


def store_context(body, member, address_id, *, lock=False, allow_address_pending=False):
    from stores.access import get_store
    from stores.models import Store
    if 'diningSessionId' in body:
        if member is None:
            raise ValueError('请登录后扫描餐桌码。')
        if set(body) & {'storeId', 'deliveryMode', 'addressId'}:
            raise ValueError('餐桌订单由扫码自动带入地址和前置仓，请勿混用个人地址或交付方式。')
        session_id = body['diningSessionId']
        if not isinstance(session_id, str):
            raise ValueError('扫码访问编号格式不正确，请重新扫码。')
        try:
            UUID(session_id)
        except ValueError as exc:
            raise ValueError('扫码访问编号格式不正确，请重新扫码。') from exc
        from dining_alliance.access import checkout_context
        store, dining = checkout_context(session_id, member, lock=lock)
        if store.delivery_fee_fen is None:
            raise ValueError('服务前置仓配送费尚未配置。')
        return store, 'DELIVERY', {**_store_snapshot(store), 'dining': dining}
    store_id, mode = body.get('storeId'), body.get('deliveryMode', '')
    if store_id is None:
        if mode:
            raise ValueError('请先选择前置仓。')
        return None, '', {}
    try:
        identifier = UUID(store_id) if isinstance(store_id, str) else None
    except ValueError as exc:
        raise ValueError('前置仓编号格式不正确。') from exc
    if not identifier:
        raise ValueError('前置仓编号格式不正确。')
    if lock:
        # Keep policy stable until stock reservation commits.
        Store.objects.select_for_update().filter(pk=identifier).first()
    store = get_store(identifier, for_sale=True)
    if mode not in ('PICKUP', 'DELIVERY', 'EXPRESS') or mode not in store.supported_modes:
        raise ValueError('请选择该前置仓支持的交付方式。')
    snapshot = _store_snapshot(store)
    if mode == 'DELIVERY':
        if store.delivery_fee_fen is None or not store.delivery_radius_meters:
            raise ValueError('前置仓配送费用或范围尚未配置。')
        if store.latitude is None or store.longitude is None:
            raise ValueError('前置仓定位尚未配置，无法验证配送范围。')
        addresses = CustomerAddress.objects.select_for_update() if lock else CustomerAddress.objects
        address = addresses.filter(pk=address_id, member=member, active=True).first() if member and address_id else None
        pending_reason = ''
        if not address or address.latitude is None or address.longitude is None:
            pending_reason = '配送到家需要选择已定位的收货地址。'
        elif distance_meters(store.latitude, store.longitude, address.latitude, address.longitude) > store.delivery_radius_meters:
            pending_reason = '收货地址不在该前置仓配送范围内，请更换地址或交付方式。'
        if pending_reason:
            if not allow_address_pending:
                raise ValueError(pending_reason)
            snapshot['deliveryEligibilityPending'] = True
            snapshot['deliveryMessage'] = pending_reason
            return store, mode, snapshot
        snapshot['addressRevision'] = address.revision
        snapshot['addressLatitude'] = str(address.latitude)
        snapshot['addressLongitude'] = str(address.longitude)
    return store, mode, snapshot


def store_shipping_fee(store, mode, express_fee):
    return 0 if mode == 'PICKUP' else store.delivery_fee_fen if mode == 'DELIVERY' else express_fee


def _store_snapshot(store):
    return {'id': str(store.id), 'name': store.name, 'address': store.address,
                'fulfillmentWarehouse': {'id': str(store.warehouse_id), 'name': store.warehouse.name},
                'contactPhone': store.contact_phone, 'revision': store.revision,
                'latitude': str(store.latitude) if store.latitude is not None else None,
                'longitude': str(store.longitude) if store.longitude is not None else None,
                'deliveryRadiusMeters': store.delivery_radius_meters,
                'deliveryFeeFen': store.delivery_fee_fen}
