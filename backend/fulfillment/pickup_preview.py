"""Read-only credential preview; final handoff rechecks the same guards."""
import hmac
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers
from common.http import response, error, method, parse_json_object
from customers.auth import require_member
from stores.access import require_staff
from stores.views import guarded
from .codes import normalized_code
from .models import StoreDelivery
from .service import FulfillmentError, _locked_order, _paid, _aftersale_guard
from .store_service import pickup_code


def preview(member, store_id, order_id, body):
    if not isinstance(body, dict) or set(body) != {'pickupCode'}:
        raise FulfillmentError('请提供自提凭证。')
    with transaction.atomic():
        require_staff(member, store_id, permission='orders')
        order = _locked_order(order_id)
        from stores.models import StoreStaff
        list(StoreStaff.objects.select_for_update().filter(store_id=store_id, member=member))
        require_staff(member, store_id, permission='orders')
        if str(order.store_id) != str(store_id):
            raise FulfillmentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
        _paid(order)
        if order.delivery_mode != 'PICKUP':
            raise FulfillmentError('该订单不是到店自提。', 'DELIVERY_MODE_MISMATCH', 409)
        row = StoreDelivery.objects.filter(order=order).first()
        if row and row.status == 'COMPLETED':
            raise FulfillmentError('该订单已完成自提，请勿重复交付。', 'ALREADY_PICKED_UP', 409)
        if not row or row.mode != 'PICKUP' or row.status != 'READY':
            raise FulfillmentError('该订单尚未备好，请先完成备货。', 'INVALID_TRANSITION', 409)
        code = normalized_code(body['pickupCode'])
        if not code or not hmac.compare_digest(code, pickup_code(order.id, row.nonce)):
            raise FulfillmentError('凭证与当前订单不匹配，请重新扫描或输入。', 'PICKUP_CODE_INVALID', 409)
        from aftersales.guards import assert_shippable_locked
        lines = _aftersale_guard(assert_shippable_locked, order, list(order.lines.filter(fulfillment_kind='SHIP')))
        from aftersales.read import line_summaries
        facts = line_summaries([line.id for line in lines])
        items = [{'orderLineId': str(line.id), 'deliverableQuantity': line.quantity - facts.get(line.id, {}).get('refundedQuantity', 0)} for line in lines]
        return {'verified': True, 'orderId': str(order.id), 'revision': order.revision,
                'deliveryStatus': 'WAITING_PICKUP', 'items': items}


@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization', 'Cookie')
@guarded
def pickup_preview_view(request, store_id, order_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    member, bad = require_member(request)
    if bad:
        return bad
    try:
        return response(request, preview(member, store_id, order_id, parse_json_object(request)))
    except FulfillmentError as exc:
        return error(request, exc.status, exc.code, str(exc))
