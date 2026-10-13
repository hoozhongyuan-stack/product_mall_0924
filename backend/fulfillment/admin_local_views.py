"""Admin list handoffs reuse store rules and never expose customer credentials."""
from django.db import transaction
from django.views.decorators.cache import cache_control
from accounts.security import audit, parse_json, permissions, require, response, error
from common.http import method
from orders.models import Order
from orders.queries import admin_order_data
from .views import _key, _fail
from .service import FulfillmentError
from .store_service import operate
from .pickup_preview import preview
from .models import Shipment, StoreDeliveryEvent
from .tracking_service import read_tracking


def _order(order_id):
    order = Order.objects.filter(pk=order_id).first()
    if order is None:
        raise FulfillmentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
    return order


@cache_control(private=True, no_store=True)
def local_fulfillment_view(request, order_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, denied = require(request, 'fulfillment.ship')
    if denied:
        return denied
    try:
        body, key = parse_json(request), _key(request)
        if body.get('action') not in ('PREPARE', 'DISPATCH', 'COMPLETE'):
            raise FulfillmentError('后台本地履约操作不正确。')
        order = _order(order_id)
        with transaction.atomic():
            operate(None, order.store_id, order_id, body, key, admin_actor=actor)
            # One audit fact per immutable event, including transport retries.
            event = StoreDeliveryEvent.objects.get(request_key=key)
            from accounts.models import AuditLog
            if not AuditLog.objects.filter(action_code='fulfillment.local.' + event.action.lower(), object_type='store_delivery_event', object_id=str(event.id)).exists():
                audit(request, 'fulfillment.local.' + event.action.lower(), 'store_delivery_event', event.id, actor=actor,
                      after={'orderId': str(order_id), 'storeId': str(order.store_id), 'action': event.action, 'note': event.note})
        order.refresh_from_db()
        return response(request, admin_order_data(order))
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


@cache_control(private=True, no_store=True)
def pickup_preview_view(request, order_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, denied = require(request, 'fulfillment.ship')
    if denied:
        return denied
    try:
        order = _order(order_id)
        return response(request, preview(None, order.store_id, order_id, parse_json(request), admin_actor=actor))
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


@cache_control(private=True, no_store=True)
def tracking_view(request, order_id):
    bad = method(request, 'GET')
    if bad:
        return bad
    actor, denied = require(request)
    if denied:
        return denied
    if not {'order.read', 'fulfillment.read'}.intersection(permissions(actor)):
        return error(request, 403, 'PERMISSION_DENIED', '当前账号没有此操作权限。')
    try:
        _order(order_id)
        shipment = Shipment.objects.filter(order_id=order_id).first()
        if shipment is None:
            raise FulfillmentError('订单尚未发货。', 'NOT_SHIPPED', 404)
        return response(request, read_tracking(shipment.id))
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)


@cache_control(private=True, no_store=True)
def redemption_use_view(request, order_id, voucher_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, denied = require(request, 'fulfillment.redeem')
    if denied:
        return denied
    try:
        from accounts.security import operation_permissions
        from .models import RedeemVoucher
        from .service import _locked_order, redeem
        from .views import _body
        body, key = _body(request, ('quantity', 'expectedRevision')), _key(request)
        with transaction.atomic():
            from aftersales.returns import _identity_lock
            _identity_lock('redeem-use', key, 'REDEMPTION_USE')
            order = _locked_order(order_id)
            if 'fulfillment.redeem' not in operation_permissions(actor):
                raise FulfillmentError('当前账号没有此操作权限。', 'PERMISSION_DENIED', 403)
            voucher_order = RedeemVoucher.objects.filter(pk=voucher_id).values_list('order_line__order_id', flat=True).first()
            if voucher_order is None:
                raise FulfillmentError('核销凭证不存在。', 'VOUCHER_NOT_FOUND', 404)
            if voucher_order != order.id:
                raise FulfillmentError('凭证不属于当前订单，请核对顾客出示的凭证。', 'VOUCHER_ORDER_MISMATCH', 409)
            data = redeem(voucher_id, actor, body['quantity'], body['expectedRevision'], key)
            if not data['replayed']:
                audit(request, 'fulfillment.redeem.use', 'redeem_voucher', voucher_id, actor=actor,
                      after={'orderId': str(order_id), 'quantity': body['quantity'], 'eventId': data['eventId']})
            order.refresh_from_db()
            return response(request, {**data, 'order': admin_order_data(order)})
    except (FulfillmentError, ValueError) as exc:
        return _fail(request, exc)
