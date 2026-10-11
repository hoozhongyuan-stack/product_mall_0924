"""Bounded order read models shared by member and authorized PC views."""
from django.db.models import Exists, OuterRef
from payments.models import OfflinePaymentReport
from payments.offline import reconciliation_data
from payments.service import PaymentError
from .attribution import attribution_data, filter_attribution
from .models import Order
from .service import order_data
from fulfillment.read import page_fulfillment_summaries
from fulfillment.models import ShipmentCorrection, StoreDeliveryEvent
from fulfillment.task_reads import TASK_FIELDS, filter_order_tasks


def review_status(order, reported):
    return 'PAID' if order.status == 'PAID' else 'CLOSED' if order.status == 'CLOSED' else 'PENDING_REVIEW' if reported else 'UNREPORTED'


def list_orders(params, member=None):
    task = params.get('fulfillment', '')
    if (task and task not in TASK_FIELDS) or (
            hasattr(params, 'getlist') and len(params.getlist('fulfillment')) > 1):
        raise PaymentError('履约待办筛选参数不正确。')
    try:
        page, size = int(params.get('page', '1')), int(params.get('pageSize', '20'))
    except ValueError:
        raise PaymentError('分页参数不正确。')
    if not 1 <= page <= 10000 or not 1 <= size <= 100:
        raise PaymentError('分页参数超出范围。')
    rows = Order.objects.annotate(reported=Exists(OfflinePaymentReport.objects.filter(order_id=OuterRef('pk'))))
    if member:
        rows = rows.filter(member=member)
    if 'orderIds' in params:
        from uuid import UUID
        raw_ids = params.get('orderIds')
        if (not isinstance(raw_ids, str) or not raw_ids or len(raw_ids) > 3700
                or (hasattr(params, 'getlist') and len(params.getlist('orderIds')) != 1)):
            raise PaymentError('订单编号筛选参数不正确。')
        identifiers = raw_ids.split(',')
        if not 1 <= len(identifiers) <= 100:
            raise PaymentError('订单编号筛选只支持 1 至 100 个不同编号。')
        try:
            identifiers = [UUID(value.strip()) for value in identifiers]
        except (ValueError, AttributeError) as exc:
            raise PaymentError('订单编号筛选参数不正确。') from exc
        if len(set(identifiers)) != len(identifiers):
            raise PaymentError('订单编号筛选不能包含重复编号。')
        rows = rows.filter(id__in=identifiers)
    store_id = params.get('storeId')
    if store_id:
        from uuid import UUID
        try:
            rows = rows.filter(store_id=UUID(store_id))
        except (ValueError, TypeError):
            raise PaymentError('前置仓编号不正确。')
    rows = filter_attribution(rows, params)
    order_kind=params.get('orderKind')
    if order_kind:
        if order_kind not in {'CASH','POINTS'}:raise PaymentError('订单类型不正确。')
        rows=rows.filter(order_kind=order_kind)
    status, payment_method = params.get('status'), params.get('paymentMethod')
    if status:
        if status not in Order.Status.values:
            raise PaymentError('订单状态不正确。')
        rows = rows.filter(status=status)
    if payment_method:
        if payment_method not in Order.PaymentMethod.values:
            raise PaymentError('支付方式不正确。')
        rows = rows.filter(payment_method=payment_method)
    reported = params.get('reported')
    if reported is not None:
        if reported not in ('true', 'false'):
            raise PaymentError('报告筛选参数不正确。')
        rows = rows.filter(reported=reported == 'true')
    search = params.get('search', '')
    if len(search) > 40:
        raise PaymentError('订单号搜索不超过 40 字。')
    if search:
        rows = rows.filter(order_no__icontains=search.strip())
    if task:
        rows = filter_order_tasks(rows, task)
    total = rows.count()
    page_rows = list(rows.order_by('-created_at', '-id')[(page-1)*size:page*size])
    summaries = page_fulfillment_summaries(page_rows)
    items = [{**attribution_data(o), 'orderId': str(o.id), 'orderNo': o.order_no, 'status': o.status, 'paymentMethod': o.payment_method,
              'storeId': str(o.store_id) if o.store_id else None,
              'storeName': o.store_snapshot.get('name') if o.store_id else None, 'deliveryMode': o.delivery_mode or None,
              'dining': {key: o.store_snapshot['dining'][key] for key in ('restaurantId', 'name', 'address', 'tableNumber')} if o.store_snapshot.get('dining') else None,
              'orderKind':o.order_kind,'exchangePoints':o.points_to_use if o.order_kind=='POINTS' else 0,
              'revision': o.revision, 'payableFen': o.payable_fen, 'createdAt': o.created_at.isoformat(),
              'expiresAt': o.expires_at.isoformat(), 'paymentReviewStatus': review_status(o, o.reported),
              **summaries[o.id]} for o in page_rows]
    return {'items': items, 'page': page, 'pageSize': size, 'total': total}


def admin_order_data(order):
    data = order_data(order)
    if data['shipment']:
        corrections = ShipmentCorrection.objects.filter(shipment_id=data['shipment']['shipmentId']) \
            .select_related('actor').order_by('-corrected_at', '-id')[:20]
        data['shipment']['corrections'] = [
            {'correctionId': str(c.id), 'oldCarrierCode': c.old_carrier_code,
             'oldCarrierName': c.old_carrier_name, 'oldTrackingNo': c.old_tracking_no,
             'newCarrierCode': c.new_carrier_code, 'newCarrierName': c.new_carrier_name,
             'newTrackingNo': c.new_tracking_no, 'reason': c.reason,
             'actorName': c.actor.display_name, 'correctedAt': c.corrected_at.isoformat()}
            for c in corrections]
        data['shipment']['correctionCount'] = ShipmentCorrection.objects.filter(
            shipment_id=data['shipment']['shipmentId']).count()
    receipts = order.paymentreceipt_set.select_related('anomaly').order_by('-recorded_at')[:50]
    data['receipts'] = [{'receiptId': str(r.id), 'merchantAccountId': r.merchant_account_id,
        'externalTradeNo': r.external_trade_no, 'amountFen': r.amount_fen, 'paidAt': r.paid_at.isoformat(),
        'recordedAt': r.recorded_at.isoformat(), 'appliedAt': r.applied_at.isoformat() if r.applied_at else None,
        'anomaly': {'reason': r.anomaly.reason, 'status': r.anomaly.status} if hasattr(r, 'anomaly') else None} for r in receipts]
    data['confirmations'] = [reconciliation_data(r) for r in order.offline_reconciliations.select_related('actor').order_by('-created_at')[:50]]
    data['receiptCount'] = order.paymentreceipt_set.count()
    data['confirmationCount'] = order.offline_reconciliations.count()
    local_events = StoreDeliveryEvent.objects.filter(order=order).select_related('actor', 'admin_actor').order_by('-occurred_at', '-id')
    data['localDeliveryEvents'] = [
        {'eventId': str(event.id), 'requestKey': str(event.request_key), 'action': event.action, 'note': event.note,
         'actorName': (event.admin_actor.display_name if event.admin_actor_id else (event.actor.nickname or '前置仓工作人员')),
         'actorKind': 'ADMIN' if event.admin_actor_id else 'STORE_STAFF',
         'occurredAt': event.occurred_at.isoformat()}
        for event in local_events[:20]]
    data['localDeliveryEventCount'] = local_events.count()
    return data
