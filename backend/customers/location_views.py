"""Private member location lookup; bounded cross-worker quotas, no credentials in response."""
from uuid import UUID
from django.db import transaction
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers
from common.http import response, error, method
from .auth import require_member
from .models import MemberProfileQuota
from . import location_service


def quota(request, member):
    window = timezone.now().replace(second=0, microsecond=0)
    with transaction.atomic():
        row, _ = MemberProfileQuota.objects.select_for_update().get_or_create(
            member=member, scope='address-map', defaults={'window_start': window})
        count = row.count if row.window_start == window else 0
        if count >= 60:
            result = error(request, 429, 'RATE_LIMITED', '查询过于频繁，请稍后再试。')
            result['Retry-After'] = '60'
            return result
        MemberProfileQuota.objects.filter(pk=row.pk).update(window_start=window, count=count + 1)
    return None


def eligibility(store_id, latitude, longitude):
    from stores.access import get_store, StoreError
    from checkout.store_context import distance_meters
    try:
        identifier = UUID(store_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('前置仓编号格式不正确。') from exc
    try:
        store = get_store(identifier, for_sale=True)
    except StoreError:
        return {'code': 'UNAVAILABLE', 'message': '该前置仓当前无法配送，请更换前置仓。'}
    if ('DELIVERY' not in store.supported_modes or store.latitude is None or store.longitude is None
            or not store.delivery_radius_meters or store.delivery_fee_fen is None):
        return {'code': 'UNAVAILABLE', 'message': '该前置仓配送服务暂不可用，请更换交付方式。'}
    if latitude is None or longitude is None:
        return {'code': 'UNLOCATED', 'message': '需补全收货位置后确认配送范围。'}
    distance = distance_meters(store.latitude, store.longitude, latitude, longitude)
    if distance > store.delivery_radius_meters:
        return {'code': 'OUT_OF_RANGE', 'message': '超出当前前置仓配送范围，请更换地址或交付方式。', 'distanceMeters': round(distance)}
    return {'code': 'ELIGIBLE', 'message': '当前前置仓可配送', 'distanceMeters': round(distance)}


@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization', 'Cookie')
def location_view(request, action):
    if action not in ('search', 'reverse', 'eligibility'):
        return error(request, 404, 'NOT_FOUND', '该地图接口不存在。')
    bad = method(request, 'GET')
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    bad = quota(request, member)
    if bad:
        return bad
    try:
        if action == 'search':
            query, city = request.GET.get('q', '').strip(), request.GET.get('city', '').strip()
            if not 2 <= len(query) <= 120 or len(city) > 80:
                raise ValueError('请输入 2 至 120 字的地点名称。')
            result = {'items': location_service.search(query, city)}
        else:
            latitude, longitude = location_service.coordinates(request.GET.get('latitude'), request.GET.get('longitude'))
            if action == 'reverse':
                if latitude is None:
                    raise ValueError('请选择收货位置。')
                result = location_service.reverse(latitude, longitude)
            else:
                result = eligibility(request.GET.get('storeId'), latitude, longitude)
        return response(request, result)
    except location_service.LocationError as exc:
        return error(request, exc.status, exc.code, str(exc))
    except ValueError as exc:
        return error(request, 400, 'VALIDATION_FAILED', str(exc))
