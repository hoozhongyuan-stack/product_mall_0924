"""Permission-scoped full-store queue counts; never derived from a page of orders."""
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers
from common.http import response, method
from customers.auth import require_member
from stores.access import StoreError, staff_permissions
from stores.views import guarded
from orders.models import Order
from aftersales.task_reads import active_cases
from .task_reads import order_task_counts


@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization', 'Cookie')
@guarded
def overview_view(request, store_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    permissions = staff_permissions(member, store_id)
    if request.GET:
        raise StoreError('工作台概览不接受查询参数。')
    stats = None
    if 'orders' in permissions:
        rows = Order.objects.filter(store_id=store_id)
        counts = order_task_counts(rows)
        stats = {key: counts[key] for key in (
            'waitingPreparation', 'waitingPickup', 'waitingDelivery', 'waitingShipment')}
        stats = {**stats, 'totalOrders': rows.count(),
            'activeAfterSales': active_cases().filter(order_line__order__store_id=store_id).count()}
    return response(request, {'storeId': str(store_id), 'permissions': permissions, 'stats': stats})
