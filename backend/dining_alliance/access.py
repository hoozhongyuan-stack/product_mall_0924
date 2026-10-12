"""Live member, staff and scanned-session boundaries."""
from django.utils import timezone
from .models import Restaurant, RestaurantStaff, DiningSession, RestaurantProduct


class DiningError(ValueError):
    def __init__(self, message, code='DINING_INVALID', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def get_restaurant(identity, lock=False):
    rows = Restaurant.objects
    if lock:
        rows = rows.select_for_update(of=('self',))
    row = rows.select_related('assigned_store__warehouse').filter(pk=identity).first()
    if not row:
        raise DiningError('餐饮店不存在。', 'DINING_NOT_FOUND', 404)
    return row


def require_staff(member, restaurant_id, permission=None, lock=False):
    row = get_restaurant(restaurant_id, lock)
    staff = RestaurantStaff.objects.filter(restaurant=row, member=member, enabled=True, member__enabled=True, member__auth_version=member.auth_version).first()
    if staff is None or (permission and permission not in staff.permissions):
        raise DiningError('没有该餐饮店的操作权限。', 'DINING_FORBIDDEN', 403)
    return row


def get_session(session_id, member, lock=False):
    rows = DiningSession.objects
    if lock:
        rows = rows.select_for_update(of=('self',))
    row = rows.select_related('table').filter(pk=session_id, member=member, member_auth_version=member.auth_version, member__enabled=True, member__auth_version=member.auth_version).first()
    if row is None or row.closed_at or row.expires_at <= timezone.now():
        raise DiningError('请重新扫描餐桌码进入店铺。', 'DINING_SCAN_REQUIRED', 403)
    return row


def checkout_context(session_id, member, lock=False):
    from stores.service import distance_meters
    from .models import DiningTable
    session = get_session(session_id, member, lock)
    restaurant = get_restaurant(session.table.restaurant_id, lock)
    table = DiningTable.objects.select_for_update().get(pk=session.table_id) if lock else session.table
    store = restaurant.assigned_store
    if lock:
        from stores.models import Store
        store = Store.objects.select_for_update().select_related('warehouse').get(pk=store.pk)
    if session.store_id != store.pk or session.restaurant_revision != restaurant.revision or session.table_revision != table.revision or session.store_revision != store.revision:
        raise DiningError("店铺设置已变化，请重新扫码。", "DINING_SCAN_REQUIRED", 409)
    if not restaurant.enabled or not table.enabled or not store.enabled or not store.accepting_orders or not store.warehouse.enabled or 'DELIVERY' not in store.supported_modes:
        raise DiningError('该餐桌或服务前置仓暂不可下单。', 'DINING_UNAVAILABLE', 409)
    from shipping.service import delivery_point_eligibility
    message = delivery_point_eligibility(store, {'latitude':float(restaurant.latitude),'longitude':float(restaurant.longitude)})
    if message:
        raise DiningError(message, 'DINING_OUT_OF_RANGE', 409)
    return store, {'sessionId': str(session.id), 'restaurantId': str(restaurant.id), 'tableId': str(table.id), 'tableNumber': table.number, 'name': restaurant.name, 'contactPhone': restaurant.contact_phone, 'address': restaurant.address, 'latitude': float(restaurant.latitude), 'longitude': float(restaurant.longitude), 'revision': restaurant.revision, 'tableRevision': table.revision, 'storeRevision': store.revision}


def sale_product_ids(context):
    restaurant_id = context['restaurantId'] if isinstance(context, dict) else context.table.restaurant_id
    return RestaurantProduct.objects.filter(restaurant_id=restaurant_id, on_sale=True, product__status='ON_SALE', product__fulfillment_kind='SHIP').values_list('product_id', flat=True)


def assert_products(session_id, member, product_ids, lock=False):
    store, context = checkout_context(session_id, member, lock)
    rows = RestaurantProduct.objects.filter(restaurant_id=context['restaurantId'], product_id__in=product_ids)
    if lock:
        rows = rows.select_for_update(of=('self',))
    allowed = {str(row.product_id) for row in rows if row.on_sale and row.product.status == 'ON_SALE' and row.product.fulfillment_kind == 'SHIP'}
    from stores.access import sale_product_ids as store_products
    allowed &= {str(value) for value in store_products(store)}
    if set(map(str, product_ids)) - allowed:
        raise DiningError('餐饮店或前置仓商品已下架，请刷新。', 'DINING_PRODUCT_UNAVAILABLE', 409)
    return store, context
