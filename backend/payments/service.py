"""Record trusted funds first, then apply them through the order-owned transition.

Only a verified provider adapter or an authorized offline reconciliation flow may
call this module. C0 deliberately exposes no public payment confirmation API.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connection, transaction
from django.utils import timezone

from orders.models import Order
from orders.service import close_expired_order_locked, settle_paid_order_locked

from .models import PaymentAnomaly, PaymentAnomalyEvent, PaymentEvent, PaymentReceipt


class PaymentError(ValueError):
    def __init__(self, message, code="PAYMENT_INVALID", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class VerifiedPayment:
    order_no: str
    channel: str
    merchant_account_id: str
    external_trade_no: str
    amount_fen: int
    paid_at: datetime
    source: str
    event_id: str = ""


def _clean_text(value, limit):
    return (isinstance(value, str) and 1 <= len(value) <= limit and
            value == value.strip() and not any(ord(char) < 32 for char in value))


def _validate(evidence):
    if not isinstance(evidence, VerifiedPayment):
        raise PaymentError("到账凭证格式不正确。")
    if (not _clean_text(evidence.order_no, 40) or
            not _clean_text(evidence.merchant_account_id, 80) or
            not _clean_text(evidence.external_trade_no, 128) or
            not isinstance(evidence.event_id, str) or
            (evidence.event_id and not _clean_text(evidence.event_id, 128)) or
            not isinstance(evidence.channel, str) or not isinstance(evidence.source, str) or
            type(evidence.amount_fen) is not int or not 0 < evidence.amount_fen <= 9_223_372_036_854_775_807 or
            not isinstance(evidence.paid_at, datetime) or
            not timezone.is_aware(evidence.paid_at)):
        raise PaymentError("到账凭证字段不正确。")
    sources = {
        "WECHAT": {"WECHAT_NOTIFICATION", "WECHAT_QUERY"},
        "OFFLINE": {"OFFLINE_RECONCILIATION"},
    }
    if evidence.source not in sources.get(evidence.channel, set()):
        raise PaymentError("到账凭证渠道与来源不一致。")
    if evidence.source == "WECHAT_NOTIFICATION" and not evidence.event_id:
        raise PaymentError("微信通知缺少事件标识。")


def _digest(evidence):
    fields = {"orderNo": evidence.order_no, "channel": evidence.channel,
              "merchant": evidence.merchant_account_id,
              "tradeNo": evidence.external_trade_no, "amountFen": evidence.amount_fen,
              "paidAt": evidence.paid_at.isoformat(), "source": evidence.source}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ingest(evidence):
    """Commit the money fact before any stock or entitlement mutation starts."""
    with transaction.atomic():
        order = Order.objects.filter(order_no=evidence.order_no).only("id").first()
        receipt, _ = PaymentReceipt.objects.get_or_create(
            channel=evidence.channel, merchant_account_id=evidence.merchant_account_id,
            external_trade_no=evidence.external_trade_no,
            defaults={"order": order, "order_no": evidence.order_no,
                      "amount_fen": evidence.amount_fen, "paid_at": evidence.paid_at,
                      "source": evidence.source,
                      "ready_for_settlement": not bool(evidence.event_id)})
        if receipt.order_no != evidence.order_no or receipt.amount_fen != evidence.amount_fen:
            _open_anomaly(receipt, PaymentAnomaly.Reason.IDENTIFIER_CONFLICT,
                          {"conflictingOrderNo": evidence.order_no,
                           "conflictingAmountFen": evidence.amount_fen})
            return receipt.id
        if not evidence.event_id:
            receipt = PaymentReceipt.objects.select_for_update().get(pk=receipt.id)
            if not PaymentAnomaly.objects.filter(receipt=receipt, status=PaymentAnomaly.Status.OPEN,
                                                 reason=PaymentAnomaly.Reason.IDENTIFIER_CONFLICT).exists():
                receipt.ready_for_settlement = True
                receipt.save(update_fields=["ready_for_settlement"])
    # Event identity conflicts must not undo the already committed money fact.
    if evidence.event_id:
        with transaction.atomic():
            event, _ = PaymentEvent.objects.get_or_create(
                channel=evidence.channel, merchant_account_id=evidence.merchant_account_id,
                event_id=evidence.event_id,
                defaults={"receipt": receipt, "evidence_digest": _digest(evidence)})
            receipt = PaymentReceipt.objects.select_for_update().get(pk=receipt.id)
            if event.receipt_id != receipt.id or event.evidence_digest != _digest(evidence):
                _open_anomaly(receipt, PaymentAnomaly.Reason.IDENTIFIER_CONFLICT,
                              {"conflictingEventId": evidence.event_id})
                receipt.ready_for_settlement = False
            elif not PaymentAnomaly.objects.filter(receipt=receipt, status=PaymentAnomaly.Status.OPEN,
                                                   reason=PaymentAnomaly.Reason.IDENTIFIER_CONFLICT).exists():
                receipt.ready_for_settlement = True
            receipt.save(update_fields=["ready_for_settlement"])
    return receipt.id


def _open_anomaly(receipt, reason, details=None):
    PaymentReceipt.objects.select_for_update().get(pk=receipt.id)
    anomaly, _ = PaymentAnomaly.objects.get_or_create(receipt=receipt, defaults={"reason": reason})
    changed = anomaly.reason != reason or anomaly.status != PaymentAnomaly.Status.OPEN
    if changed:
        anomaly.reason = reason
        anomaly.status = PaymentAnomaly.Status.OPEN
        anomaly.resolved_at = None
        anomaly.resolution_note = ""
        anomaly.save(update_fields=["reason", "status", "resolved_at", "resolution_note"])
    if changed or not anomaly.history.exists():
        PaymentAnomalyEvent.objects.create(anomaly=anomaly, action="OPEN" if not changed else "RECLASSIFY",
                                          reason=reason, status=anomaly.status, details=details or {})
    return anomaly


def _resolve_anomaly(receipt):
    anomaly = PaymentAnomaly.objects.filter(receipt=receipt, status=PaymentAnomaly.Status.OPEN).first()
    if anomaly:
        anomaly.status = PaymentAnomaly.Status.RESOLVED
        anomaly.resolved_at = timezone.now()
        anomaly.resolution_note = "结算重试成功"
        anomaly.save(update_fields=["status", "resolved_at", "resolution_note"])
        PaymentAnomalyEvent.objects.create(anomaly=anomaly, action="RESOLVE",
                                          reason=anomaly.reason, status=anomaly.status)


def _result(receipt, order, outcome):
    return {"receiptId": str(receipt.id), "outcome": outcome,
            "orderStatus": order.status if order else None}


def _apply(receipt_id):
    with transaction.atomic():
        # Close/cancel paths start with this same order row, then lock stock and benefits.
        order_no = PaymentReceipt.objects.only("order_no").get(pk=receipt_id).order_no
        order = Order.objects.select_for_update().filter(order_no=order_no).first()
        receipt = PaymentReceipt.objects.select_for_update().get(pk=receipt_id)
        if PaymentAnomaly.objects.filter(receipt=receipt, status=PaymentAnomaly.Status.OPEN,
                                         reason=PaymentAnomaly.Reason.IDENTIFIER_CONFLICT).exists():
            return _result(receipt, order, "ANOMALY")
        if receipt.applied_at:
            return _result(receipt, order, "PAID")
        if not receipt.ready_for_settlement:
            return _result(receipt, order, "PENDING")
        if order and receipt.order_id is None:
            receipt.order = order
            receipt.save(update_fields=["order"])
        if not order:
            _open_anomaly(receipt, PaymentAnomaly.Reason.UNKNOWN_ORDER)
            return _result(receipt, None, "ANOMALY")
        close_expired_order_locked(order)
        if order.status == Order.Status.CLOSED:
            reason = PaymentAnomaly.Reason.CLOSED_ORDER
        elif order.status == Order.Status.PAID:
            reason = PaymentAnomaly.Reason.ALREADY_PAID
        elif receipt.channel != order.payment_method:
            reason = PaymentAnomaly.Reason.METHOD_MISMATCH
        elif receipt.amount_fen != order.payable_fen:
            reason = PaymentAnomaly.Reason.AMOUNT_MISMATCH
        else:
            settle_paid_order_locked(order)
            receipt.applied_at = timezone.now()
            receipt.save(update_fields=["applied_at"])
            from notifications.service import ORDER_PAID, record_event
            record_event(ORDER_PAID, receipt.id, order.member_id, occurred_at=receipt.applied_at)
            _resolve_anomaly(receipt)
            return _result(receipt, order, "PAID")
        _open_anomaly(receipt, reason)
        return _result(receipt, order, "ANOMALY")


def _record_failure(receipt_id):
    with transaction.atomic():
        receipt = PaymentReceipt.objects.select_for_update().get(pk=receipt_id)
        blocking = PaymentAnomaly.objects.filter(receipt=receipt, status=PaymentAnomaly.Status.OPEN).exclude(
            reason=PaymentAnomaly.Reason.SETTLEMENT_FAILED).exists()
        if not receipt.applied_at and not blocking:
            _open_anomaly(receipt, PaymentAnomaly.Reason.SETTLEMENT_FAILED)


def record_verified_payment(evidence):
    """Apply a trusted funds fact idempotently; never erase it on settlement failure."""
    if connection.in_atomic_block:
        raise PaymentError("到账记录入口不能嵌套在外部事务中。", "PAYMENT_TRANSACTION_NESTED", 500)
    _validate(evidence)
    receipt_id = _ingest(evidence)
    return settle_recorded_payment(receipt_id)


def settle_recorded_payment(receipt_id):
    """Recovery entry for an already committed receipt; it needs no client evidence."""
    if connection.in_atomic_block:
        raise PaymentError("到账记录入口不能嵌套在外部事务中。", "PAYMENT_TRANSACTION_NESTED", 500)
    if not isinstance(receipt_id, UUID):
        raise PaymentError("到账凭证 ID 格式不正确。")
    if not PaymentReceipt.objects.filter(pk=receipt_id).exists():
        raise PaymentError("到账凭证不存在。", "PAYMENT_RECEIPT_NOT_FOUND", 404)
    try:
        return _apply(receipt_id)
    except Exception as exc:
        _record_failure(receipt_id)
        raise PaymentError("到账已记录，订单结算待重试或核查。", "PAYMENT_SETTLEMENT_FAILED", 503) from exc
