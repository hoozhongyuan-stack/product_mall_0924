"""C1 member statements and immutable, authorized reconciliation intents."""
import hashlib
from datetime import timedelta
from uuid import UUID
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from accounts.security import audit, confirm_action
from orders.models import Order
from orders.service import order_data
from .models import OfflinePaymentReport, OfflineReconciliation, PaymentReceipt
from .service import PaymentError, VerifiedPayment, record_verified_payment


def request_key(request):
    try:
        return UUID(request.headers.get('Idempotency-Key', ''))
    except (ValueError, AttributeError):
        raise PaymentError('请提供 UUID 格式的防重复请求标识。')


def clean_text(value, limit, multiline=False):
    return (isinstance(value, str) and 1 <= len(value.strip()) <= limit and
            not any(ord(c) < 32 and (not multiline or c not in '\n\r\t') for c in value))


def report_payment(member, order_id, body, key):
    if set(body) != {'note'} or not clean_text(body['note'], 500, True):
        raise PaymentError('请填写不超过 500 字的付款情况。')
    note = body['note'].strip()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id, member_id=member.id).first()
        if order is None:
            raise PaymentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
        prior = OfflinePaymentReport.objects.filter(order=order, key=key).first()
        if prior:
            if prior.note != note:
                raise PaymentError('重复标识对应不同报告。', 'IDEMPOTENCY_CONFLICT', 409)
            return order_data(order, include_voucher_code=True), True
        if (order.payment_method != 'OFFLINE' or order.status != 'PENDING_PAYMENT' or
                order.expires_at <= timezone.now()):
            raise PaymentError('当前订单不能报告线下付款，请先刷新订单状态。', 'ORDER_NOT_REPORTABLE', 409)
        if order.payment_reports.count() >= 20:
            raise PaymentError('付款报告已达上限，请联系店铺核实。', 'REPORT_LIMIT_REACHED', 429)
        OfflinePaymentReport.objects.create(order=order, key=key, note=note)
        order.revision += 1
        order.save(update_fields=['revision'])
        return order_data(order, include_voucher_code=True), False


def reconciliation_data(row):
    return {'reconciliationId': str(row.id), 'confirmationObjectId': str(row.id), 'confirmationRevision': 1,
            'actorId': str(row.actor_id), 'actorName': row.actor.display_name, 'merchantAccountId': row.merchant_account_id,
            'externalTradeNo': row.external_trade_no, 'amountFen': row.amount_fen,
            'paidAt': row.paid_at.isoformat(), 'note': row.note, 'createdAt': row.created_at.isoformat(),
            'authorizedAt': row.authorized_at.isoformat() if row.authorized_at else None,
            'outcome': row.outcome, 'receiptId': str(row.receipt_id) if row.receipt_id else None}


def _evidence_body(body):
    keys = {'expectedRevision', 'merchantAccountId', 'externalTradeNo', 'amountFen', 'paidAt', 'note', 'verified'}
    if set(body) != keys or body.get('verified') is not True:
        raise PaymentError('请核对真实收款账户、流水、到账金额和时间，并确认核实完成。')
    if (type(body['expectedRevision']) is not int or body['expectedRevision'] < 1 or
            type(body['amountFen']) is not int or not 0 < body['amountFen'] <= 9_223_372_036_854_775_807 or
            not clean_text(body['merchantAccountId'], 80) or not clean_text(body['externalTradeNo'], 128) or
            not isinstance(body['note'], str) or len(body['note']) > 500 or
            any(ord(c) < 32 and c not in '\n\r\t' for c in body['note']) or not isinstance(body['paidAt'], str)):
        raise PaymentError('到账核对字段不正确。')
    try:
        paid_at = parse_datetime(body['paidAt'])
    except ValueError:
        paid_at = None
    if not paid_at or not timezone.is_aware(paid_at) or paid_at > timezone.now():
        raise PaymentError('到账时间须为带时区且不晚于当前时间的有效时间。')
    return {'expected_revision': body['expectedRevision'], 'merchant_account_id': body['merchantAccountId'].strip(),
            'external_trade_no': body['externalTradeNo'].strip(), 'amount_fen': body['amountFen'],
            'paid_at': paid_at, 'note': body['note'].strip()}


def prepare_reconciliation(request, actor, order_id, body, key):
    values = _evidence_body(body)
    with transaction.atomic():
        # Lock actor+key independently of the order, matching the unique key scope.
        lock_key = int.from_bytes(hashlib.sha256(f'OFFLINE_RECONCILE:{actor.id}:{key}'.encode()).digest()[:8], 'big', signed=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])
        prior = OfflineReconciliation.objects.select_related('actor').filter(actor=actor, key=key).first()
        if prior:
            if prior.order_id != order_id or any(getattr(prior, name) != value for name, value in values.items()):
                raise PaymentError('重复标识对应不同到账资料。', 'IDEMPOTENCY_CONFLICT', 409)
            return reconciliation_data(prior), True
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None:
            raise PaymentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
        if order.payment_method != 'OFFLINE':
            raise PaymentError('该订单不是线下支付订单。', 'METHOD_MISMATCH', 409)
        if order.revision != values['expected_revision']:
            raise PaymentError('订单已变化，请刷新后重新核对。', 'REVISION_CONFLICT', 409)
        if values['amount_fen'] != order.payable_fen and not values['note']:
            raise PaymentError('到账金额与应付金额不符，请填写原因；将进入异常待处理。')
        # Closed or already-paid orders may still receive real funds: C0 routes
        # these to anomalies. A draft never changes their order state.
        snapshot_account = order.payment_instructions.get('merchantAccountId')
        if not snapshot_account or values['merchant_account_id'] != snapshot_account:
            raise PaymentError('实际收款账户与订单付款说明不一致，请先核查。', 'MERCHANT_ACCOUNT_MISMATCH', 409)
        if OfflineReconciliation.objects.filter(actor=actor, created_at__gte=timezone.now()-timedelta(minutes=15)).count() >= 60:
            raise PaymentError('核对请求过多，请稍后重试。', 'RATE_LIMITED', 429)
        row = OfflineReconciliation.objects.create(order=order, actor=actor, key=key, **values)
        audit(request, 'payment.offline.prepare', 'offline_reconciliation', row.id, actor,
              after={'orderId': str(order.id), 'amountFen': row.amount_fen})
        return reconciliation_data(row), False


def confirm_reconciliation(request, actor, reconciliation_id):
    if connection.in_atomic_block:
        raise PaymentError('确认入口不能嵌套事务。', 'PAYMENT_TRANSACTION_NESTED', 500)
    with transaction.atomic():
        row = OfflineReconciliation.objects.select_related('order', 'actor').select_for_update(of=('self',)).filter(
            pk=reconciliation_id, actor=actor).first()
        if row is None:
            raise PaymentError('核对记录不存在或不可访问。', 'RECONCILIATION_NOT_FOUND', 404)
        if row.authorized_at is None:
            denied = confirm_action(request, actor, 'payment.offline.confirm', row.id, 1)
            if denied:
                return None, denied
            row.authorized_at = timezone.now()
            row.outcome = 'AUTHORIZED'
            row.save(update_fields=['authorized_at', 'outcome'])
            audit(request, 'payment.offline.authorize', 'offline_reconciliation', row.id, actor,
                  after={'orderId': str(row.order_id), 'amountFen': row.amount_fen})
    evidence = VerifiedPayment(order_no=row.order.order_no, channel='OFFLINE',
        merchant_account_id=row.merchant_account_id, external_trade_no=row.external_trade_no,
        amount_fen=row.amount_fen, paid_at=row.paid_at, source='OFFLINE_RECONCILIATION')
    try:
        result = record_verified_payment(evidence)
    except PaymentError:
        _save_outcome(request, actor, row, 'FAILED')
        raise
    _save_outcome(request, actor, row, result['outcome'])
    return result, None


def _save_outcome(request, actor, row, outcome):
    with transaction.atomic():
        receipt = PaymentReceipt.objects.filter(channel='OFFLINE', merchant_account_id=row.merchant_account_id,
                                                external_trade_no=row.external_trade_no).first()
        current = OfflineReconciliation.objects.select_for_update().get(pk=row.id)
        # Concurrent failed request must not overwrite successful completion.
        if current.outcome == 'PAID' and outcome == 'FAILED':
            return
        changed = current.outcome != outcome or current.receipt_id != (receipt.id if receipt else None)
        if changed:
            current.outcome = outcome
            current.receipt = receipt
            current.save(update_fields=['outcome', 'receipt'])
            audit(request, 'payment.offline.result', 'offline_reconciliation', row.id, actor,
                  after={'outcome': outcome, 'receiptId': str(receipt.id) if receipt else None},
                  result='SUCCESS' if outcome == 'PAID' else outcome)
