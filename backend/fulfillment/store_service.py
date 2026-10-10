"""Store staff handoff operations; caller authorization and order locks are mandatory."""
import base64
import hashlib
import hmac
import json
import uuid
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Shipment, StoreDelivery, StoreDeliveryEvent


def pickup_code(order_id, nonce):
    payload = f'STORE-PHYSICAL-PICKUP:{order_id}:{nonce}'.encode('ascii')
    raw = hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).digest()[:17]
    return base64.b32encode(raw).decode().rstrip('=')[:26]


def local_status(mode, row):
    if row is None:
        return 'WAITING_PREPARATION'
    if row.status == 'READY':
        return 'WAITING_PICKUP' if mode == 'PICKUP' else 'WAITING_DELIVERY'
    return row.status


def physical_handoff_fact(order):
    row = Shipment.objects.filter(order_id=order.id).first()
    if row is not None:
        return row
    row = StoreDelivery.objects.filter(order_id=order.id).first()
    return row if row and row.status in ('IN_TRANSIT', 'COMPLETED') else None


def delivery_data(order, *, include_code=False):
    if getattr(order, 'delivery_mode', '') not in ('PICKUP', 'DELIVERY'):
        return None
    row = StoreDelivery.objects.filter(order=order).first()
    result = {'mode': order.delivery_mode, 'status': local_status(order.delivery_mode, row),
        'preparedAt': row.prepared_at.isoformat() if row else None,
        'dispatchedAt': row.dispatched_at.isoformat() if row and row.dispatched_at else None,
        'completedAt': row.completed_at.isoformat() if row and row.completed_at else None}
    if include_code and row and row.mode == 'PICKUP' and row.status == 'READY' and order.status == 'PAID':
        result['pickupCode'] = pickup_code(order.id, row.nonce)
        from .qr import voucher_qr_data_url
        result['pickupQrDataUrl'] = voucher_qr_data_url(result['pickupCode'])
    return result


def operate(member, store_id, order_id, body, request_key):
    from .service import FulfillmentError, _locked_order, _paid, _revision, _aftersale_guard
    from stores.access import require_staff
    from .codes import new_nonce, normalized_code
    if not isinstance(request_key, uuid.UUID):
        raise FulfillmentError('请提供防重复请求标识。')
    allowed = {'action', 'expectedRevision', 'pickupCode', 'note', 'carrierCode', 'trackingNo'}
    if not isinstance(body, dict) or set(body) - allowed or not {'action', 'expectedRevision'} <= set(body):
        raise FulfillmentError('请求字段不正确。')
    action = body['action']
    note = body.get('note', '')
    if action not in ('PREPARE', 'DISPATCH', 'COMPLETE', 'SHIP') or not isinstance(note, str) or len(note.strip()) > 500:
        raise FulfillmentError('履约操作不正确。')
    digest = hashlib.sha256(json.dumps({'storeId': str(store_id), 'orderId': str(order_id), **body}, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        from aftersales.returns import _identity_lock
        _identity_lock('store-fulfillment', request_key, 'STORE_FULFILLMENT')
        require_staff(member, store_id, permission='orders')
        order = _locked_order(order_id)
        from stores.models import StoreStaff
        list(StoreStaff.objects.select_for_update().filter(store_id=store_id, member=member))
        require_staff(member, store_id, permission='orders')
        if str(order.store_id) != str(store_id):
            raise FulfillmentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
        previous = StoreDeliveryEvent.objects.filter(request_key=request_key).first()
        if previous:
            if previous.actor_id != member.id or previous.order_id != order.id or previous.request_digest != digest:
                raise FulfillmentError('防重复标识对应不同请求。', 'IDEMPOTENCY_CONFLICT', 409)
            return delivery_data(order)
        _paid(order)
        _revision(order, body['expectedRevision'])
        if action == 'SHIP':
            _ship_express_locked(order, member, body, request_key)
            StoreDeliveryEvent.objects.create(order=order, actor=member, action=action, request_key=request_key, request_digest=digest, note=note.strip())
            order.revision += 1
            order.save(update_fields=['revision'])
            return delivery_data(order)
        if order.delivery_mode not in ('PICKUP', 'DELIVERY'):
            raise FulfillmentError('快递订单请使用发货操作。', 'DELIVERY_MODE_MISMATCH', 409)
        from aftersales.guards import assert_shippable_locked
        _aftersale_guard(assert_shippable_locked, order, list(order.lines.filter(fulfillment_kind='SHIP')))
        row = StoreDelivery.objects.select_for_update().filter(order=order).first()
        now = timezone.now()
        if action == 'PREPARE':
            if row:
                raise FulfillmentError('订单已经备货。', 'ALREADY_PREPARED', 409)
            row = StoreDelivery.objects.create(order=order, mode=order.delivery_mode, status='READY', nonce=new_nonce(), prepared_at=now)
        elif action == 'DISPATCH':
            if not row or row.mode != 'DELIVERY' or row.status != 'READY':
                raise FulfillmentError('请先备货，只有配送订单可以出发。', 'INVALID_TRANSITION', 409)
            row.status, row.dispatched_at = 'IN_TRANSIT', now
            row.save(update_fields=['status', 'dispatched_at'])
        else:
            if not row or row.status not in ('READY', 'IN_TRANSIT'):
                raise FulfillmentError('订单当前不可确认完成。', 'INVALID_TRANSITION', 409)
            if row.mode == 'PICKUP':
                code = normalized_code(body.get('pickupCode'))
                if not code or not hmac.compare_digest(code, pickup_code(order.id, row.nonce)):
                    raise FulfillmentError('自提码不正确。', 'PICKUP_CODE_INVALID', 409)
            elif row.status != 'IN_TRANSIT' or len(note.strip()) < 5:
                raise FulfillmentError('请先开始配送，并填写至少 5 字的交付说明。', 'DELIVERY_EVIDENCE_REQUIRED', 409)
            row.status, row.completed_at = 'COMPLETED', now
            row.save(update_fields=['status', 'completed_at'])
        StoreDeliveryEvent.objects.create(order=order, actor=member, action=action, request_key=request_key, request_digest=digest, note=note.strip())
        order.revision += 1
        order.save(update_fields=['revision'])
        if row.status == 'COMPLETED':
            from orders.benefit_lifecycle import reconcile_benefits_locked
            reconcile_benefits_locked(order, source_ref='store-delivery:' + str(order.id))
        return delivery_data(order)


def _ship_express_locked(order, member, body, request_key):
    from datetime import timedelta
    from .service import FulfillmentError, _carrier, _tracking, _aftersale_guard
    from .models import OrderFulfillmentSnapshot
    from aftersales.guards import assert_shippable_locked
    if order.delivery_mode != 'EXPRESS' or Shipment.objects.filter(order=order).exists():
        raise FulfillmentError('订单不可重复发货或交付方式不匹配。', 'INVALID_TRANSITION', 409)
    if Shipment.objects.filter(request_key=request_key).exists():
        raise FulfillmentError('防重复标识已用于其他发货请求。', 'IDEMPOTENCY_CONFLICT', 409)
    carrier = _carrier(body.get('carrierCode'))
    tracking = _tracking(body.get('trackingNo'))
    lines = _aftersale_guard(assert_shippable_locked, order, list(order.lines.filter(fulfillment_kind='SHIP')))
    warehouses = {line.warehouse_id for line in lines}
    if len(warehouses) != 1:
        raise FulfillmentError('发货仓快照不一致。', 'WAREHOUSE_MISMATCH', 409)
    snapshot = OrderFulfillmentSnapshot.objects.filter(order=order).first()
    if not snapshot:
        raise FulfillmentError('订单缺少履约时限快照。', 'FULFILLMENT_SNAPSHOT_MISSING', 409)
    now = timezone.now()
    shipment = Shipment.objects.create(order=order, carrier=carrier, carrier_code=carrier.code,
        carrier_name=carrier.name, tracking_no=tracking, warehouse_id=next(iter(warehouses)),
        shipped_by_member=member, shipped_at=now, auto_confirm_days_snapshot=snapshot.auto_confirm_days,
        auto_confirm_at=now + timedelta(days=snapshot.auto_confirm_days), request_key=request_key)
    from notifications.service import ORDER_SHIPPED, record_event
    record_event(ORDER_SHIPPED, shipment.id, order.member_id, occurred_at=now)
