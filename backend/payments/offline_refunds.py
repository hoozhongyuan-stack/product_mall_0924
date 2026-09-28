"""D1 dual staff refund reconciliation, independent funds commit and safe recovery."""

import hashlib
from datetime import timedelta
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from accounts.models import AdminAccount
from accounts.security import permissions, audit, confirm_action
from .models import OfflineRefundReconciliation, RefundEvidence
from .refunds import (
    prepare_refund,
    VerifiedRefund,
    record_verified_refund,
    RefundError,
    _lock_case,
    _permit,
)
from .offline import clean_text


def reconciliation_data(row, actor=None):
    effective = "SUCCEEDED" if row.intent.status == "SUCCEEDED" else row.outcome
    from .models import RefundAnomaly

    if (
        RefundAnomaly.objects.filter(evidence__intent=row.intent, open=True)
        .exclude(reason="SETTLEMENT_FAILED")
        .exists()
    ):
        effective = "ANOMALY"
    return {
        "reconciliationId": str(row.id),
        "confirmationObjectId": str(row.id),
        "confirmationRevision": 1,
        "preparedById": str(row.prepared_by_id),
        "preparedByName": row.prepared_by.display_name,
        "merchantAccountId": row.merchant_account_id,
        "externalRefundNo": row.external_refund_no,
        "amountFen": row.amount_fen,
        "refundedAt": row.refunded_at.isoformat(),
        "refundMethod": row.refund_method,
        "proofReference": row.proof_reference,
        "note": row.note,
        "refundNo": row.intent.refund_no,
        "originalTradeNo": row.intent.original_trade_no,
        "intentStatus": row.intent.status,
        "authorizedAt": row.authorized_at.isoformat() if row.authorized_at else None,
        "authorizedByName": (
            row.authorized_by.display_name if row.authorized_by_id else None
        ),
        "outcome": effective,
        "createdAt": row.created_at.isoformat(),
        "canConfirm": bool(
            actor
            and actor.id != row.prepared_by_id
            and ("refund.offline.confirm" in permissions(actor))
            and (effective not in ("SUCCEEDED", "ANOMALY"))
            and (not row.authorized_by_id or row.authorized_by_id == actor.id)
        ),
    }


def clean_body(body):
    keys = {
        "expectedRevision",
        "merchantAccountId",
        "externalRefundNo",
        "amountFen",
        "refundedAt",
        "refundMethod",
        "proofReference",
        "note",
        "verified",
    }
    if set(body) != keys or body.get("verified") is not True:
        raise RefundError("请核对实际退款账户、流水、金额和时间并确认核实。")
    if (
        type(body["expectedRevision"]) is not int
        or body["expectedRevision"] < 1
        or type(body["amountFen"]) is not int
        or (not 0 < body["amountFen"] <= 9223372036854775807)
        or (not clean_text(body["merchantAccountId"], 80))
        or (not clean_text(body["externalRefundNo"], 128))
        or (not clean_text(body["proofReference"], 128))
        or ("://" in body["proofReference"])
        or body["proofReference"]
        .strip()
        .lower()
        .startswith(("//", "http:", "https:", "data:", "javascript:", "file:", "ftp:"))
        or (
            body["refundMethod"]
            not in (
                "BANK_TRANSFER",
                "WECHAT_TRANSFER",
                "ALIPAY_TRANSFER",
                "CASH",
                "OTHER",
            )
        )
        or (not isinstance(body["note"], str))
        or (len(body["note"]) > 500)
        or any((ord(c) < 32 and c not in "\n\r\t" for c in body["note"]))
        or (not isinstance(body["refundedAt"], str))
    ):
        raise RefundError("退款核对字段不正确。")
    try:
        at = parse_datetime(body["refundedAt"])
    except ValueError:
        at = None
    if not at or not timezone.is_aware(at) or at > timezone.now():
        raise RefundError("退款时间须带时区且不晚于当前时间。")
    return {
        "expected_revision": body["expectedRevision"],
        "merchant_account_id": body["merchantAccountId"].strip(),
        "external_refund_no": body["externalRefundNo"].strip(),
        "amount_fen": body["amountFen"],
        "refunded_at": at,
        "refund_method": body["refundMethod"],
        "proof_reference": body["proofReference"].strip(),
        "note": body["note"].strip(),
    }


def prepare_reconciliation(request, actor, case_id, body, key):
    values = clean_body(body)
    actor = _permit(actor, "refund.prepare")
    with transaction.atomic():
        for identity in (
            f"REFUND_DRAFT:{actor.id}:{key}",
            f"REFUND_TRANSFER:{values['merchant_account_id']}:{values['external_refund_no']}",
        ):
            lock = int.from_bytes(
                hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True
            )
            with connection.cursor() as c:
                c.execute("SELECT pg_advisory_xact_lock(%s)", [lock])
        prior = (
            OfflineRefundReconciliation.objects.select_related(
                "prepared_by", "intent", "authorized_by"
            )
            .filter(prepared_by=actor, request_key=key)
            .first()
        )
        if prior:
            if prior.intent.case_id != case_id or any(
                (getattr(prior, n) != v for n, v in values.items())
            ):
                raise RefundError(
                    "请求标识对应其他退款资料。", "IDEMPOTENCY_CONFLICT", 409
                )
            return (reconciliation_data(prior, actor), True)
        order, case = _lock_case(case_id)
        if order.payment_method != "OFFLINE":
            raise RefundError("微信订单须原路退款。", "METHOD_MISMATCH", 409)
        if (
            case.revision != values["expected_revision"]
            or case.status != "WAITING_REFUND"
        ):
            raise RefundError("售后已变化或尚不可退款。", "REFUND_NOT_READY", 409)
        if OfflineRefundReconciliation.objects.filter(intent__case=case).exists():
            raise RefundError(
                "已有不可改写的退款核对记录。", "REFUND_DRAFT_EXISTS", 409
            )
        if OfflineRefundReconciliation.objects.filter(
            merchant_account_id=values["merchant_account_id"],
            external_refund_no=values["external_refund_no"],
        ).exists():
            raise RefundError("退款流水已用于其他记录。", "REFUND_TRANSFER_EXISTS", 409)
        if (
            OfflineRefundReconciliation.objects.filter(
                prepared_by=actor,
                created_at__gte=timezone.now() - timedelta(minutes=15),
            ).count()
            >= 60
        ):
            raise RefundError("核对请求过多。", "RATE_LIMITED", 429)
        intent = prepare_refund(case.id, actor, key)
        if intent.created_by_id != actor.id:
            raise RefundError(
                "只能由原退款登记人保存核对资料。", "REFUND_PREPARER_MISMATCH", 403
            )
        if (
            values["amount_fen"] != intent.amount_fen
            or values["merchant_account_id"] != intent.merchant_account_id
        ):
            raise RefundError(
                "实际退款金额和账户须与原收款及审核金额一致。",
                "REFUND_IDENTITY_MISMATCH",
                409,
            )
        row = OfflineRefundReconciliation.objects.create(
            intent=intent, prepared_by=actor, request_key=key, **values
        )
        audit(
            request,
            "refund.offline.prepare",
            "offline_refund_reconciliation",
            row.id,
            actor,
            after={"caseId": str(case.id), "amountFen": row.amount_fen},
        )
        return (reconciliation_data(row, actor), False)


def confirm_reconciliation(request, actor, row_id):
    if connection.in_atomic_block:
        raise RefundError("确认入口不能嵌套事务。", "REFUND_TRANSACTION_NESTED", 500)
    actor = _permit(actor, "refund.offline.confirm")
    identity = (
        OfflineRefundReconciliation.objects.filter(pk=row_id)
        .values_list("intent__case_id", flat=True)
        .first()
    )
    if not identity:
        raise RefundError("核对记录不存在。", "REFUND_DRAFT_NOT_FOUND", 404)
    with transaction.atomic():
        _lock_case(identity)
        row = (
            OfflineRefundReconciliation.objects.select_related("intent", "prepared_by")
            .select_for_update(of=("self",))
            .get(pk=row_id)
        )
        live = AdminAccount.objects.select_for_update().get(pk=actor.pk)
        if not live.enabled or "refund.offline.confirm" not in permissions(live):
            raise RefundError("当前账号无退款确认权限。", "PERMISSION_DENIED", 403)
        if actor.id == row.prepared_by_id or actor.id == row.intent.created_by_id:
            raise RefundError(
                "须由不同授权人员复核实际退款。", "REFUND_DUAL_CONTROL_REQUIRED", 403
            )
        if row.authorized_by_id and row.authorized_by_id != actor.id:
            raise RefundError(
                "该记录已由另一复核员授权，请联系原复核员恢复。",
                "REFUND_CONFIRM_ACTOR_MISMATCH",
                403,
            )
        if not row.authorized_at:
            denied = confirm_action(request, actor, "refund.offline.confirm", row.id, 1)
            if denied:
                return (None, denied)
            row.authorized_by = actor
            row.authorized_at = timezone.now()
            row.outcome = "AUTHORIZED"
            row.save(update_fields=["authorized_by", "authorized_at", "outcome"])
            audit(
                request,
                "refund.offline.authorize",
                "offline_refund_reconciliation",
                row.id,
                actor,
                after={"amountFen": row.amount_fen},
            )
    result = record_verified_refund(
        VerifiedRefund(
            refund_no=row.intent.refund_no,
            channel="OFFLINE",
            merchant_account_id=row.merchant_account_id,
            original_trade_no=row.intent.original_trade_no,
            external_refund_no=row.external_refund_no,
            amount_fen=row.amount_fen,
            refunded_at=row.refunded_at,
            source="OFFLINE_RECONCILIATION",
            confirmed_by_id=actor.id,
        )
    )
    with transaction.atomic():
        current = OfflineRefundReconciliation.objects.select_for_update().get(pk=row.id)
        if current.outcome != "SUCCEEDED":
            current.outcome = result["outcome"]
            current.save(update_fields=["outcome"])
        audit(
            request,
            "refund.offline.result",
            "offline_refund_reconciliation",
            row.id,
            actor,
            after={"outcome": result["outcome"]},
            result="SUCCESS" if result["outcome"] == "SUCCEEDED" else "FAILED",
        )
    return (result, None)
