"""Bounded order read models shared by member and authorized PC views."""
from django.db.models import Exists, OuterRef
from payments.models import OfflinePaymentReport
from payments.offline import reconciliation_data
from payments.service import PaymentError
from .models import Order
from .service import order_data
from fulfillment.read import page_fulfillment_summaries
from fulfillment.models import ShipmentCorrection
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
    items = [{'orderId': str(o.id), 'orderNo': o.order_no, 'status': o.status, 'paymentMethod': o.payment_method,
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
    return data
