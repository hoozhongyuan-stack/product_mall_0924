"""Trusted refund adapter boundary. D0 exposes no public funds endpoint or transport.

Evidence commits before finalization; recovery never dispatches another refund.
Every business transaction locks Order first, matching fulfillment and aftersales.
"""
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.utils import timezone

from accounts.models import AdminAccount
from accounts.security import permissions
from orders.models import Order
from .access import applied_receipt
from .models import (RefundIntent, RefundOperation, RefundEvidence, RefundNotification,
                     RefundAnomaly, RefundHistory)

logger = logging.getLogger(__name__)


class RefundError(ValueError):
    def __init__(self, message, code="REFUND_INVALID", status=400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class VerifiedRefund:
    refund_no: str
    channel: str
    merchant_account_id: str
    original_trade_no: str
    external_refund_no: str
    amount_fen: int
    refunded_at: datetime
    source: str
    event_id: str = ""
    confirmed_by_id: uuid.UUID | None = None


def _permit(actor, code):
    live = AdminAccount.objects.filter(pk=getattr(actor, "pk", None), enabled=True).first()
    if live is None or code not in permissions(live):
        raise RefundError("当前账号没有退款操作权限。", "PERMISSION_DENIED", 403)
    return live


def _lock_case(case_id):
    from aftersales.models import AfterSaleCase
    order_id = AfterSaleCase.objects.filter(pk=case_id).values_list("order_line__order_id", flat=True).first()
    if order_id is None:
        raise RefundError("售后申请不存在。", "AFTERSALE_NOT_FOUND", 404)
    order = Order.objects.select_for_update().get(pk=order_id)
    from customers.consumption import lock_consumption_member
    lock_consumption_member(order.member_id)
    case = AfterSaleCase.objects.select_for_update().select_related("order_line").get(pk=case_id)
    return order, case


def prepare_refund(case_id, actor, request_key):
    actor = _permit(actor, "refund.prepare")
    if not isinstance(request_key, uuid.UUID):
        raise RefundError("退款请求标识须为 UUID。")
    with transaction.atomic():
        order, case = _lock_case(case_id)
        actor = AdminAccount.objects.select_for_update().get(pk=actor.pk)
        if not actor.enabled or "refund.prepare" not in permissions(actor):
            raise RefundError("当前账号没有退款操作权限。", "PERMISSION_DENIED", 403)
        prior = RefundIntent.objects.filter(created_by=actor, request_key=request_key).first()
        if prior and prior.case_id != case.id:
            raise RefundError("请求标识对应另一笔退款。", "IDEMPOTENCY_CONFLICT", 409)
        intent = RefundIntent.objects.filter(case=case).first()
        if intent:
            return intent
        if case.status != "WAITING_REFUND" or order.status != "PAID":
            raise RefundError("当前售后不能发起资金退款。", "REFUND_NOT_READY", 409)
        from aftersales.refund_amounts import freeze_refund_amount_locked
        amount = freeze_refund_amount_locked(order,case).total_fen
        if case.status != "WAITING_REFUND" or order.status != "PAID" or amount <= 0:
            raise RefundError("当前售后不能发起资金退款。", "REFUND_NOT_READY", 409)
        receipt = applied_receipt(order)
        if receipt is None:
            raise RefundError("订单缺少已结算收款凭证。", "PAYMENT_EVIDENCE_MISSING", 409)
        intent = RefundIntent.objects.create(case=case, receipt=receipt,
            refund_no="R" + uuid.uuid4().hex.upper(), channel=receipt.channel,
            merchant_account_id=receipt.merchant_account_id, original_trade_no=receipt.external_trade_no,
            amount_fen=amount, created_by=actor, request_key=request_key)
        RefundHistory.objects.create(intent=intent, action="PREPARED")
        return intent


def begin_refund_operation(intent_id, kind):
    """Adapter-only lease; caller performs I/O after this transaction returns."""
    if kind not in ("DISPATCH", "QUERY"):
        raise RefundError("退款操作类型不正确。")
    with transaction.atomic():
        initial = RefundIntent.objects.get(pk=intent_id)
        _lock_case(initial.case_id)
        intent = RefundIntent.objects.select_for_update().get(pk=intent_id)
        if kind == "DISPATCH" and RefundEvidence.objects.filter(intent=intent).exists():
            raise RefundError("已有资金凭证，须恢复结算或核查异常。", "REFUND_SETTLEMENT_REQUIRED", 409)
        if intent.status == "SUCCEEDED":
            raise RefundError("退款已完成。", "REFUND_SUCCEEDED", 409)
        now = timezone.now()
        if intent.lease_until and intent.lease_until > now:
            raise RefundError("退款正在处理中。", "REFUND_BUSY", 409)
        if intent.active_operation_id:
            # An expired dispatch may have reached the channel; query before retry.
            intent.status = "UNKNOWN"
        if kind == "DISPATCH" and intent.status not in ("PREPARED", "FAILED"):
            raise RefundError("退款结果未知，须先查单。", "REFUND_QUERY_REQUIRED", 409)
        operation = RefundOperation.objects.create(intent=intent, kind=kind)
        intent.active_operation_id, intent.lease_until = operation.id, now + timedelta(seconds=30)
        intent.status = "PROCESSING"
        intent.save(update_fields=["active_operation_id", "lease_until", "status"])
        return operation


def finish_refund_operation(operation_id, outcome, failure_code=""):
    if outcome not in ("UNKNOWN", "FAILED", "PROCESSING") or not re.fullmatch(r"[A-Z0-9_]{0,40}", failure_code):
        raise RefundError("退款结果字段不正确。")
    with transaction.atomic():
        initial = RefundOperation.objects.select_related("intent").get(pk=operation_id)
        _lock_case(initial.intent.case_id)
        intent = RefundIntent.objects.select_for_update().get(pk=initial.intent_id)
        operation = RefundOperation.objects.select_for_update().get(pk=operation_id)
        if operation.finished_at:
            return intent.status
        stale = intent.status == "SUCCEEDED" or intent.active_operation_id != operation.id
        operation.outcome = "STALE" if stale else outcome
        operation.failure_code, operation.finished_at = failure_code, timezone.now()
        operation.save(update_fields=["outcome", "failure_code", "finished_at"])
        if not stale:
            intent.status, intent.active_operation_id, intent.lease_until = outcome, None, None
            intent.save(update_fields=["status", "active_operation_id", "lease_until"])
            RefundHistory.objects.create(intent=intent, action=outcome)
        return intent.status


def _text(value, limit):
    return isinstance(value, str) and 1 <= len(value) <= limit and value == value.strip() and all(ord(c) >= 32 for c in value)


def _validate(evidence):
    if not isinstance(evidence, VerifiedRefund):
        raise RefundError("退款凭证格式不正确。")
    if (not all(_text(value, limit) for value, limit in (
            (evidence.refund_no, 40), (evidence.merchant_account_id, 80),
            (evidence.original_trade_no, 128), (evidence.external_refund_no, 128))) or
            type(evidence.amount_fen) is not int or not 0 < evidence.amount_fen <= 2**63 - 1 or
            not isinstance(evidence.refunded_at, datetime) or not timezone.is_aware(evidence.refunded_at) or
            not isinstance(evidence.event_id, str) or (evidence.event_id and not _text(evidence.event_id, 128))):
        raise RefundError("退款凭证字段不正确。")
    if evidence.channel == "OFFLINE":
        if evidence.source != "OFFLINE_RECONCILIATION" or not isinstance(evidence.confirmed_by_id, uuid.UUID):
            raise RefundError("线下退款缺少授权复核。")
        checker = AdminAccount.objects.filter(pk=evidence.confirmed_by_id, enabled=True).first()
        if checker is None or "refund.offline.confirm" not in permissions(checker):
            raise RefundError("当前账号没有退款复核权限。", "PERMISSION_DENIED", 403)
        intent = RefundIntent.objects.filter(refund_no=evidence.refund_no).first()
        if intent and intent.created_by_id == checker.id:
            raise RefundError("线下退款须由不同授权人员复核。", "DUAL_REVIEW_REQUIRED", 403)
    elif evidence.channel == "WECHAT":
        if (evidence.source not in ("WECHAT_NOTIFICATION", "WECHAT_QUERY") or
                evidence.confirmed_by_id is not None or
                (evidence.source == "WECHAT_NOTIFICATION" and not evidence.event_id)):
            raise RefundError("微信退款凭证来源不正确。")
    else:
        raise RefundError("退款渠道不正确。")


def _anomaly(evidence, reason):
    anomaly, created = RefundAnomaly.objects.get_or_create(evidence=evidence, defaults={"reason": reason})
    if not created and anomaly.open and anomaly.reason == reason:
        return
    if not created:
        anomaly.reason, anomaly.open, anomaly.resolved_at = reason, True, None
        anomaly.save(update_fields=["reason", "open", "resolved_at"])
    RefundHistory.objects.create(intent=evidence.intent, evidence=evidence, action=reason)


def _ingest(evidence):
    with transaction.atomic():
        # Serialize external identities before finding their canonical order.
        # This also covers a conflicting notification naming a different intent.
        identity = json.dumps([evidence.channel, evidence.merchant_account_id,
                               evidence.external_refund_no], separators=(",", ":"))
        lock_key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])
        intent = RefundIntent.objects.select_related("case__order_line").filter(refund_no=evidence.refund_no).first()
        if intent is None:
            raise RefundError("退款业务号不存在。", "REFUND_NOT_FOUND", 404)
        existing = RefundEvidence.objects.select_related("intent__case__order_line").filter(
            channel=evidence.channel, merchant_account_id=evidence.merchant_account_id,
            external_refund_no=evidence.external_refund_no).first()
        targets = [intent, existing.intent] if existing else [intent]
        order_ids = {target.case.order_line.order_id for target in targets}
        list(Order.objects.select_for_update().filter(pk__in=order_ids).order_by("id"))
        list(RefundIntent.objects.select_for_update().filter(pk__in={target.id for target in targets}).order_by("id"))
        if evidence.channel == "OFFLINE":
            checker = AdminAccount.objects.select_for_update().filter(pk=evidence.confirmed_by_id, enabled=True).first()
            if checker is None or "refund.offline.confirm" not in permissions(checker):
                raise RefundError("当前账号没有退款复核权限。", "PERMISSION_DENIED", 403)
            if checker.id == intent.created_by_id:
                raise RefundError("线下退款须由不同授权人员复核。", "DUAL_REVIEW_REQUIRED", 403)
        row, _ = RefundEvidence.objects.get_or_create(channel=evidence.channel,
            merchant_account_id=evidence.merchant_account_id, external_refund_no=evidence.external_refund_no,
            defaults={"intent": intent, "original_trade_no": evidence.original_trade_no,
                "amount_fen": evidence.amount_fen, "refunded_at": evidence.refunded_at,
                "source": evidence.source, "confirmed_by_id": evidence.confirmed_by_id})
        row = RefundEvidence.objects.select_for_update().get(pk=row.id)
        if (row.intent_id != intent.id or row.original_trade_no != evidence.original_trade_no or
                row.amount_fen != evidence.amount_fen):
            _anomaly(row, "IDENTIFIER_CONFLICT")
        if evidence.event_id:
            digest = hashlib.sha256(json.dumps([evidence.refund_no, evidence.original_trade_no,
                evidence.external_refund_no, evidence.amount_fen], separators=(",", ":")).encode()).hexdigest()
            event, _ = RefundNotification.objects.get_or_create(channel=evidence.channel,
                merchant_account_id=evidence.merchant_account_id, event_id=evidence.event_id,
                defaults={"evidence": row, "evidence_digest": digest})
            if event.evidence_id != row.id or event.evidence_digest != digest:
                _anomaly(row, "IDENTIFIER_CONFLICT")
        return row.id


def _finalize_case(case):
    from aftersales.service import finish_refund_locked
    line = case.order_line
    if line.fulfillment_kind == "SHIP":
        from fulfillment.models import Shipment
        if Shipment.objects.filter(order_id=line.order_id).exists():
            from aftersales.models import ReturnAcceptance
            if case.kind != "RETURN_REFUND" or not ReturnAcceptance.objects.filter(case=case,refund_quantity__gt=0).exists():
                raise RefundError("已发货退款须先完成退货验收或明确免寄回。", "RETURN_NOT_ACCEPTED", 409)
        else:
            from inventory.refunds import restore_unshipped_refund_locked
            restore_unshipped_refund_locked(line, case.quantity, case.id)
    elif case.unredeemed_quantity:
        from fulfillment.service import void_refunded_quantity_locked
        void_refunded_quantity_locked(line, case.unredeemed_quantity, case.id)
    finish_refund_locked(case)


def _apply(evidence_id):
    with transaction.atomic():
        initial = RefundEvidence.objects.select_related("intent").get(pk=evidence_id)
        order, case = _lock_case(initial.intent.case_id)
        intent = RefundIntent.objects.select_for_update().get(pk=initial.intent_id)
        row = RefundEvidence.objects.select_for_update().get(pk=evidence_id)
        if intent.channel == "WECHAT":
            from .models import WechatRefundConflict
            if WechatRefundConflict.objects.filter(intent=intent).exists():
                _anomaly(row, "REFUND_EVENT_CONFLICT")
                return {"evidenceId": str(row.id), "outcome": "ANOMALY"}
        if RefundAnomaly.objects.filter(evidence=row, open=True).exclude(reason="SETTLEMENT_FAILED").exists():
            return {"evidenceId": str(row.id), "outcome": "ANOMALY"}
        if row.applied_at:
            return {"evidenceId": str(row.id), "outcome": "SUCCEEDED"}
        if (row.channel != intent.channel or row.merchant_account_id != intent.merchant_account_id or
                row.original_trade_no != intent.original_trade_no or row.amount_fen != intent.amount_fen):
            _anomaly(row, "IDENTITY_MISMATCH")
            return {"evidenceId": str(row.id), "outcome": "ANOMALY"}
        if intent.status == "SUCCEEDED":
            _anomaly(row, "DUPLICATE_FUNDS")
            return {"evidenceId": str(row.id), "outcome": "ANOMALY"}
        from aftersales.refund_amounts import total_refund
        if order.status != "PAID" or case.status != "WAITING_REFUND" or total_refund(case) != intent.amount_fen:
            _anomaly(row, "STATE_CONFLICT")
            return {"evidenceId": str(row.id), "outcome": "ANOMALY"}
        _finalize_case(case)
        now = timezone.now()
        intent.status, intent.succeeded_at = "SUCCEEDED", now
        intent.active_operation_id, intent.lease_until = None, None
        intent.save(update_fields=["status", "succeeded_at", "active_operation_id", "lease_until"])
        row.applied_at = now
        row.save(update_fields=["applied_at"])
        RefundAnomaly.objects.filter(evidence=row, reason="SETTLEMENT_FAILED", open=True).update(open=False, resolved_at=now)
        RefundHistory.objects.create(intent=intent, evidence=row, action="SUCCEEDED")
        from orders.benefit_lifecycle import reconcile_benefits_locked
        reconcile_benefits_locked(order,source_ref="case:"+str(case.id))
        order.revision += 1
        order.save(update_fields=["revision"])
        return {"evidenceId": str(row.id), "outcome": "SUCCEEDED"}


def settle_recorded_refund(evidence_id):
    if connection.in_atomic_block:
        raise RuntimeError("退款资金凭证须独立提交后再结算。")
    try:
        return _apply(evidence_id)
    except Exception as exc:
        logger.warning("Refund settlement failed (%s)", type(exc).__name__)
        with transaction.atomic():
            row = RefundEvidence.objects.select_for_update().get(pk=evidence_id)
            if row.applied_at:
                return {"evidenceId": str(row.id), "outcome": "SUCCEEDED"}
            if RefundAnomaly.objects.filter(evidence=row, open=True).exclude(reason="SETTLEMENT_FAILED").exists():
                return {"evidenceId": str(row.id), "outcome": "ANOMALY"}
            _anomaly(row, "SETTLEMENT_FAILED")
        return {"evidenceId": str(evidence_id), "outcome": "SETTLEMENT_FAILED"}


def record_verified_refund(evidence):
    if connection.in_atomic_block:
        raise RuntimeError("退款资金凭证须独立提交后再结算。")
    _validate(evidence)
    return settle_recorded_refund(_ingest(evidence))


def recover_recorded_refunds(batch_size=100):
    """Bounded local settlement only; never dispatches funds or contacts a channel."""
    if type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise RefundError("恢复批次须为 1 至 500。")
    blocked = RefundAnomaly.objects.filter(open=True).exclude(reason="SETTLEMENT_FAILED").values("evidence_id")
    ids = list(RefundEvidence.objects.filter(applied_at__isnull=True).exclude(pk__in=blocked)
               .order_by("recorded_at", "id").values_list("id", flat=True)[:batch_size])
    outcomes = [settle_recorded_refund(evidence_id)["outcome"] for evidence_id in ids]
    return {"checked": len(outcomes), "succeeded": outcomes.count("SUCCEEDED"),
            "failed": outcomes.count("SETTLEMENT_FAILED"), "anomalies": outcomes.count("ANOMALY")}
