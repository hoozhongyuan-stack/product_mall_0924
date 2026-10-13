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
        'completedAt': row.completed_at.isoformat() if row and row.completed_at else None,
        'failureReason': row.failure_reason if row else None,
        'events': [{'action': event.action, 'requestKey': str(event.request_key),
                    'createdAt': event.occurred_at.isoformat()}
                   for event in StoreDeliveryEvent.objects.filter(order=order)
                   .only('action', 'request_key', 'occurred_at').order_by('-occurred_at', '-id')[:20]]}
    if row and row.status == 'COMPLETED' and pickup_available(order):
        result['status'] = 'WAITING_PREPARATION'
    from .physical import available_physical, page_physical_quantities
    lines = list(order.lines.filter(fulfillment_kind='SHIP'))
    facts = page_physical_quantities([line.id for line in lines])
    result['canFulfill'] = order.status == 'PAID' and (any(available_physical(lines).values()) or bool(row and row.status in ('IN_TRANSIT', 'EXCEPTION') and any(facts[line.id]['dispatchedQuantity'] > facts[line.id]['deliveredQuantity'] for line in lines)))
    if include_code and row and row.mode == 'PICKUP' and row.status == 'READY' and order.status == 'PAID' and pickup_available(order):
        result['pickupCode'] = pickup_code(order.id, row.nonce)
        from .qr import voucher_qr_data_url
        result['pickupQrDataUrl'] = voucher_qr_data_url(result['pickupCode'])
    return result


def pickup_available(order):
    from .physical import available_physical
    return any(available_physical(list(order.lines.filter(fulfillment_kind='SHIP'))).values())


def operate(member, store_id, order_id, body, request_key, *, admin_actor=None):
    from .service import FulfillmentError, _locked_order, _paid, _revision, _aftersale_guard
    from stores.access import require_staff
    from .codes import new_nonce, normalized_code
    from .physical import page_physical_quantities, available_physical
    from .models import LocalHandoffLine
    if admin_actor is not None:
        raise FulfillmentError('本地交付由前置仓中心操作。', 'FULFILLMENT_OWNER_MISMATCH', 403)
    if not isinstance(request_key, uuid.UUID):
        raise FulfillmentError('请提供防重复请求标识。')
    allowed = {'action', 'expectedRevision', 'pickupCode', 'note', 'carrierCode', 'trackingNo'}
    if not isinstance(body, dict) or set(body) - allowed or not {'action', 'expectedRevision'} <= set(body):
        raise FulfillmentError('请求字段不正确。')
    action, note = body['action'], body.get('note', '')
    if action == 'SHIP':
        raise FulfillmentError('快递订单由平台总仓发货。', 'FULFILLMENT_OWNER_MISMATCH', 403)
    if action not in ('PREPARE', 'DISPATCH', 'COMPLETE', 'FAIL_DELIVERY', 'RETRY_DELIVERY') or not isinstance(note, str) or len(note.strip()) > 500:
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
        if order.delivery_mode not in ('PICKUP', 'DELIVERY'):
            raise FulfillmentError('本地交付方式与订单不一致。', 'DELIVERY_MODE_MISMATCH', 409)
        lines = list(order.lines.filter(fulfillment_kind='SHIP'))
        if not lines or any(line.warehouse_id != order.store.warehouse_id for line in lines):
            raise FulfillmentError('履约仓与扣库仓不一致。', 'WAREHOUSE_MISMATCH', 409)
        row = StoreDelivery.objects.select_for_update().filter(order=order).first()
        now = timezone.now()
        quantities = available_physical(lines)
        handoff_quantities = {}
        if action == 'PREPARE':
            if row and row.status != 'COMPLETED':
                raise FulfillmentError('订单已经备货。', 'ALREADY_PREPARED', 409)
            from aftersales.guards import assert_shippable_locked
            _aftersale_guard(assert_shippable_locked, order, lines)
            if row is None:
                row = StoreDelivery.objects.create(order=order, mode=order.delivery_mode, status='READY', nonce=new_nonce(), prepared_at=now)
            else:
                row.status, row.nonce, row.prepared_at = 'READY', new_nonce(), now
                row.dispatched_at, row.completed_at, row.failure_reason = None, None, ''
                row.save(update_fields=['status', 'nonce', 'prepared_at', 'dispatched_at', 'completed_at', 'failure_reason'])
        elif action == 'DISPATCH':
            if not row or row.mode != 'DELIVERY' or row.status != 'READY':
                raise FulfillmentError('请先备货，只有配送订单可以出发。', 'INVALID_TRANSITION', 409)
            from aftersales.guards import assert_shippable_locked
            _aftersale_guard(assert_shippable_locked, order, lines)
            handoff_quantities = quantities
            row.status, row.dispatched_at = 'IN_TRANSIT', now
            row.save(update_fields=['status', 'dispatched_at'])
        elif action == 'FAIL_DELIVERY':
            if not row or row.mode != 'DELIVERY' or row.status != 'IN_TRANSIT' or len(note.strip()) < 3:
                raise FulfillmentError('仅配送中可记录异常，请填写至少 3 字原因。', 'INVALID_TRANSITION', 409)
            row.status, row.failure_reason = 'EXCEPTION', note.strip()
            row.save(update_fields=['status', 'failure_reason'])
        elif action == 'RETRY_DELIVERY':
            if not row or row.mode != 'DELIVERY' or row.status != 'EXCEPTION':
                raise FulfillmentError('仅配送异常订单可继续配送。', 'INVALID_TRANSITION', 409)
            row.status, row.failure_reason = 'IN_TRANSIT', ''
            row.save(update_fields=['status', 'failure_reason'])
        else:
            if not row or row.status not in ('READY', 'IN_TRANSIT'):
                raise FulfillmentError('订单当前不可确认完成。', 'INVALID_TRANSITION', 409)
            if row.mode == 'PICKUP':
                code = normalized_code(body.get('pickupCode'))
                if not code or not hmac.compare_digest(code, pickup_code(order.id, row.nonce)):
                    raise FulfillmentError('自提码不正确。', 'PICKUP_CODE_INVALID', 409)
                from aftersales.guards import assert_shippable_locked
                _aftersale_guard(assert_shippable_locked, order, lines)
                handoff_quantities = quantities
            elif row.status != 'IN_TRANSIT' or len(note.strip()) < 5:
                raise FulfillmentError('请先开始配送，并填写至少 5 字的交付说明。', 'DELIVERY_EVIDENCE_REQUIRED', 409)
            else:
                facts = page_physical_quantities([line.id for line in lines])
                handoff_quantities = {line.id: facts[line.id]['dispatchedQuantity'] - facts[line.id]['deliveredQuantity'] for line in lines}
            if not any(handoff_quantities.values()):
                raise FulfillmentError('没有待交付数量。', 'NO_SHIPPING_LINES', 409)
            row.status, row.completed_at = 'COMPLETED', now
            row.save(update_fields=['status', 'completed_at'])
        event = StoreDeliveryEvent.objects.create(order=order, actor=member, action=action, request_key=request_key, request_digest=digest, note=note.strip())
        if action in ('DISPATCH', 'COMPLETE'):
            LocalHandoffLine.objects.bulk_create([LocalHandoffLine(event=event, order_line_id=identity, phase=action, quantity=qty) for identity, qty in handoff_quantities.items() if qty > 0])
        order.revision += 1
        order.save(update_fields=['revision'])
        if row.status == 'COMPLETED':
            from orders.benefit_lifecycle import reconcile_benefits_locked
            reconcile_benefits_locked(order, source_ref='store-delivery:' + str(event.id))
        return delivery_data(order)


def require_admin_operator(actor):
    from .service import FulfillmentError
    raise FulfillmentError('本地交付由前置仓中心操作。', 'FULFILLMENT_OWNER_MISMATCH', 403)
