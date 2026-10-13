"""Immutable redemption scope and shared platform/store rights consumption."""
import hmac
import hashlib
import json
import uuid
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .codes import code_digest, normalized_code
from .models import RedeemEvent, RedeemVoucher


def scope_data(line):
    snapshot = getattr(line, 'redeem_scope_snapshot', {}) or {}
    scope, stores = snapshot.get('scope', 'PLATFORM'), snapshot.get('stores', [])
    return {'redeemScope': scope, 'redeemStoreIds': [row['storeId'] for row in stores], 'redeemStores': stores}


def permitted(line, store_id=None):
    data = scope_data(line)
    return (data['redeemScope'] in ('PLATFORM', 'BOTH') if store_id is None else
            data['redeemScope'] in ('STORE', 'BOTH') and str(store_id) in data['redeemStoreIds'])


def assert_scope(line, store_id=None):
    from .service import FulfillmentError
    if not permitted(line, store_id):
        raise FulfillmentError('该订单权益不在当前核销范围内。', 'REDEMPTION_SCOPE_DENIED', 403)


def event_data(event):
    return {'eventId': str(event.id), 'kind': event.kind, 'quantity': event.quantity,
            'remainingQuantity': event.remaining_quantity, 'actorKind': 'STORE' if event.store_staff_actor_id else 'PLATFORM',
            'actorName': (event.store_staff_actor.nickname or '前置仓工作人员') if event.store_staff_actor_id else event.actor.display_name,
            'storeId': str(event.store_id) if event.store_id else None,
            'storeName': event.store.name if event.store_id else None,
            'occurredAt': event.occurred_at.isoformat(), 'redeemedAt': event.occurred_at.isoformat(),
            'reversedAt': event.reversal.occurred_at.isoformat() if event.kind == 'USE' and hasattr(event, 'reversal') else None,
            'reason': event.reason or None, 'note': event.note or None, 'requestKey': str(event.request_key) if event.request_key else None}


def store_redeem(member, store_id, order_id, body, request_key):
    from .service import FulfillmentError, _locked_order, _paid, _remaining
    from stores.access import require_staff
    from stores.models import StoreStaff
    from orders.models import OrderLine
    from aftersales.guards import reserved_unredeemed_quantity
    fields = {'orderLineId', 'voucherCode', 'quantity', 'expectedRevision', 'note'}
    if not isinstance(body, dict) or set(body) != fields or not isinstance(request_key, uuid.UUID):
        raise FulfillmentError('核销请求字段不正确。')
    quantity, note = body['quantity'], body['note']
    if type(quantity) is not int or not 1 <= quantity <= 9999 or not isinstance(note, str) or len(note.strip()) > 500:
        raise FulfillmentError('核销数量或说明不正确。')
    try:
        line_id = uuid.UUID(str(body['orderLineId']))
    except (ValueError, TypeError, AttributeError) as exc:
        raise FulfillmentError('核销订单项不正确。') from exc
    normalized = normalized_code(body['voucherCode'])
    if not normalized:
        raise FulfillmentError('核销凭证与当前订单不匹配。', 'VOUCHER_CODE_INVALID', 409)
    with transaction.atomic():
        from aftersales.returns import _identity_lock
        _identity_lock('redeem-use', request_key, 'REDEMPTION_USE')
        digest = hashlib.sha256(json.dumps([str(order_id), str(line_id), str(member.id), str(store_id), quantity, body['expectedRevision'], note.strip(), code_digest(normalized)], separators=(',', ':')).encode()).hexdigest()
        prior_key = RedeemEvent.objects.filter(request_key=request_key, kind='USE').select_related('voucher').first()
        if prior_key and (prior_key.voucher.order_line_id != line_id or prior_key.store_staff_actor_id != member.id or prior_key.store_id != store_id or prior_key.quantity != quantity or prior_key.note != note.strip() or prior_key.request_digest != digest):
            raise FulfillmentError('防重复标识对应不同核销请求。', 'IDEMPOTENCY_CONFLICT', 409)
        store = require_staff(member, store_id, permission='orders')
        if not store.enabled or not store.warehouse.enabled:
            raise FulfillmentError('当前前置仓已停用，不能核销。', 'STORE_DISABLED', 409)
        order = _locked_order(order_id)
        list(StoreStaff.objects.select_for_update().filter(store_id=store_id, member=member))
        require_staff(member, store_id, permission='orders')
        line = OrderLine.objects.filter(pk=line_id, order=order, fulfillment_kind='REDEEM').first()
        if line is None:
            raise FulfillmentError('核销订单项不存在。', 'ORDER_LINE_NOT_FOUND', 404)
        assert_scope(line, store_id)
        _paid(order)
        voucher = RedeemVoucher.objects.select_for_update().filter(order_line=line).first()
        if not voucher:
            raise FulfillmentError('核销凭证不存在。', 'VOUCHER_NOT_FOUND', 404)
        normalized = normalized_code(body['voucherCode'])
        if not normalized or not hmac.compare_digest(code_digest(normalized), voucher.code_digest):
            raise FulfillmentError('核销凭证与当前订单不匹配。', 'VOUCHER_CODE_INVALID', 409)
        prior = RedeemEvent.objects.filter(voucher=voucher, request_key=request_key, kind='USE').first()
        if prior:
            if prior.store_staff_actor_id != member.id or prior.store_id != store_id or prior.quantity != quantity or prior.note != note.strip():
                raise FulfillmentError('防重复标识对应不同请求。', 'IDEMPOTENCY_CONFLICT', 409)
            return order
        if RedeemEvent.objects.filter(store_staff_actor=member, occurred_at__gte=timezone.now()-timedelta(minutes=15)).count() >= 60:
            raise FulfillmentError('核销操作过于频繁，请稍后重试。', 'RATE_LIMITED', 429)
        if type(body['expectedRevision']) is not int or body['expectedRevision'] != voucher.revision:
            raise FulfillmentError('凭证数量已变化，请刷新后重试。', 'VOUCHER_CHANGED', 409)
        if not voucher.valid_until or voucher.valid_until < timezone.localdate():
            raise FulfillmentError('权益有效期未明确或已经到期。', 'VOUCHER_EXPIRED', 409)
        remaining = _remaining(voucher)
        if quantity > remaining - reserved_unredeemed_quantity(line.id):
            raise FulfillmentError('核销数量超过未冻结剩余数量。', 'QUANTITY_EXCEEDED', 409)
        voucher.redeemed_quantity += quantity
        voucher.revision += 1
        voucher.save(update_fields=['redeemed_quantity', 'revision'])
        event = RedeemEvent.objects.create(voucher=voucher, kind='USE', quantity=quantity, remaining_quantity=remaining-quantity,
            store_staff_actor=member, store_id=store_id, request_key=request_key, request_digest=digest, note=note.strip())
        order.revision += 1
        order.save(update_fields=['revision'])
        from orders.benefit_lifecycle import reconcile_benefits_locked
        reconcile_benefits_locked(order, source_ref='store-redeem:' + str(event.id))
        return order
