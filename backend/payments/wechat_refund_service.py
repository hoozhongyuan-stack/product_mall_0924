"""WeChat refund orchestration: leases, transaction-free I/O, durable money facts."""
import hashlib
import json
from datetime import datetime, timedelta
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from aftersales.models import AfterSaleCase
from accounts.models import AdminAccount
from accounts.security import permissions
from orders.models import Order
from .models import RefundIntent, RefundEvidence, RefundOperation, RefundHistory, WechatPaymentAttempt
from .wechat_refund_models import WechatRefundNotice, WechatRefundConflict
from .wechat_refund_gateway import WechatRefundGateway
from .wechat_config import WechatGatewayError
from .refunds import (RefundError, VerifiedRefund, begin_refund_operation,
                      finish_refund_operation, record_verified_refund)


def get_gateway():
    return WechatRefundGateway()


def _outside():
    if connection.in_atomic_block:
        raise RuntimeError("微信退款网络及资金凭证须在业务事务外处理。")


def _enabled_gateway():
    if not getattr(settings, "WECHAT_REFUND_ENABLED", False):
        raise RefundError("微信退款尚未开放，等待商户配置与验收。", "WECHAT_REFUND_DISABLED", 409)
    return get_gateway()


def _intent(intent_id):
    row = RefundIntent.objects.select_related("receipt", "case__order_line__order").filter(pk=intent_id).first()
    if not row or row.channel != "WECHAT":
        raise RefundError("微信退款单不存在。", "WECHAT_REFUND_NOT_FOUND", 404)
    return row


def _validate(payload, gateway, intent, *, notification=False):
    status = payload.get("refund_status" if notification else "status") if isinstance(payload, dict) else None
    amount = payload.get("amount") if isinstance(payload, dict) else None
    attempt = WechatPaymentAttempt.objects.filter(order_id=intent.receipt.order_id).first()
    if (status not in {"SUCCESS", "PROCESSING", "CLOSED", "ABNORMAL"} or
            intent.merchant_account_id != gateway.config.merchant_id or
            payload.get("mchid", gateway.config.merchant_id if not notification else None) != gateway.config.merchant_id or
            payload.get("out_refund_no") != intent.refund_no or
            payload.get("transaction_id") != intent.original_trade_no or
            not isinstance(payload.get("out_trade_no"), str) or not 1 <= len(payload["out_trade_no"]) <= 64 or
            (attempt and payload["out_trade_no"] != attempt.out_trade_no) or
            not isinstance(payload.get("refund_id"), str) or not 1 <= len(payload["refund_id"]) <= 128 or
            not isinstance(amount, dict) or type(amount.get("refund")) is not int or
            type(amount.get("total")) is not int or amount["refund"] != intent.amount_fen or
            amount["total"] != intent.receipt.amount_fen or amount.get("currency", "CNY" if notification else None) != "CNY"):
        raise RefundError("微信退款身份或金额与原资金凭证不一致。", "WECHAT_REFUND_IDENTITY_INVALID", 400)
    if status == "SUCCESS":
        try:
            stamp = datetime.fromisoformat(payload["success_time"].replace("Z", "+00:00"))
            if not timezone.is_aware(stamp) or stamp > timezone.now() or stamp < intent.receipt.paid_at:
                raise ValueError()
        except (KeyError, TypeError, AttributeError, ValueError):
            raise RefundError("微信退款成功时间不正确。", "WECHAT_REFUND_INVALID", 400) from None
    return status


def _accept(payload, gateway, intent, event_id=""):
    return record_verified_refund(VerifiedRefund(refund_no=intent.refund_no, channel="WECHAT",
        merchant_account_id=gateway.config.merchant_id, original_trade_no=payload["transaction_id"],
        external_refund_no=payload["refund_id"], amount_fen=payload["amount"]["refund"],
        refunded_at=datetime.fromisoformat(payload["success_time"].replace("Z", "+00:00")),
        source="WECHAT_NOTIFICATION" if event_id else "WECHAT_QUERY", event_id=event_id))


def _blocked(intent):
    if intent.wechat_conflicts.exists():
        raise RefundError("微信退款通知存在冲突，须人工核查。", "WECHAT_REFUND_EVENT_CONFLICT", 409)


def _lease(intent, kind, actor_id=None):
    with transaction.atomic():
        Order.objects.select_for_update().get(pk=intent.receipt.order_id)
        _blocked(intent)
        if actor_id is not None:
            actor = AdminAccount.objects.select_for_update().filter(pk=actor_id, enabled=True).first()
            if not actor or not {"aftersale.read", "refund.prepare"}.issubset(permissions(actor)):
                raise RefundError("当前账号没有微信退款操作权限。", "PERMISSION_DENIED", 403)
        return begin_refund_operation(intent.id, kind)


def _perform(intent, gateway, kind, actor_id=None):
    if gateway.config.merchant_id != intent.merchant_account_id:
        raise RefundError("退款商户与原收款商户不一致。", "WECHAT_REFUND_IDENTITY_INVALID", 409)
    operation = _lease(intent, kind, actor_id)
    try:
        payload = (gateway.refund(intent.refund_no, intent.original_trade_no, intent.amount_fen,
                                 intent.receipt.amount_fen) if kind == "DISPATCH" else gateway.query_refund(intent.refund_no))
        status = _validate(payload, gateway, intent)
        if status == "SUCCESS" and kind == "QUERY":
            money = _accept(payload, gateway, intent)
            outcome = "UNKNOWN" if money["outcome"] != "SUCCEEDED" else "PROCESSING"
        else:
            outcome = "FAILED" if status == "CLOSED" else "UNKNOWN" if status == "ABNORMAL" else "PROCESSING"
        finish_refund_operation(operation.id, outcome,
            "WECHAT_REFUND_" + status if status in {"CLOSED", "ABNORMAL"} else "")
    except WechatGatewayError as exc:
        definite = (kind == "QUERY" and exc.code == "WECHAT_REFUND_NOT_FOUND") or (
            kind == "DISPATCH" and exc.code == "WECHAT_REFUND_REJECTED")
        finish_refund_operation(operation.id, "FAILED" if definite else "UNKNOWN", exc.code[:40])
        if kind == "QUERY" and definite:
            return refund_data(intent.case_id)
        raise
    except RefundError as exc:
        finish_refund_operation(operation.id, "UNKNOWN", exc.code[:40])
        raise
    except Exception:
        finish_refund_operation(operation.id, "UNKNOWN", "WECHAT_REFUND_INTERNAL_ERROR")
        raise RefundError("微信退款结果未知，请先查询退款状态。", "WECHAT_REFUND_INTERNAL_ERROR", 503) from None
    return refund_data(intent.case_id)


def dispatch_intent(intent_id, actor_id=None):
    _outside()
    intent = _intent(intent_id)
    if intent.status == "SUCCEEDED":
        return refund_data(intent.case_id)
    if (WechatRefundNotice.objects.filter(intent=intent, provider_status="CLOSED").exists() or
            intent.operations.filter(failure_code="WECHAT_REFUND_CLOSED").exists()):
        raise RefundError("微信退款已关闭，须人工核查，不自动重新发起。", "WECHAT_REFUND_CLOSED", 409)
    return _perform(intent, _enabled_gateway(), "DISPATCH", actor_id)


def query_intent(intent_id, actor_id=None):
    _outside()
    intent = _intent(intent_id)
    if intent.status == "SUCCEEDED":
        return refund_data(intent.case_id)
    return _perform(intent, _enabled_gateway(), "QUERY", actor_id)


def _notice(intent, merchant_id, event_id, payload, status):
    facts = [intent.refund_no, merchant_id, payload["transaction_id"], payload["refund_id"], status, payload["amount"], payload.get("success_time")]
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with transaction.atomic():
        identity = json.dumps([merchant_id, event_id], separators=(",", ":"))
        lock = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock])
        prior = WechatRefundNotice.objects.filter(merchant_id=merchant_id, event_id=event_id).first()
        intent_ids = {intent.id, prior.intent_id} if prior else {intent.id}
        order_ids = RefundIntent.objects.filter(pk__in=intent_ids).values_list("receipt__order_id", flat=True)
        list(Order.objects.select_for_update().filter(pk__in=order_ids).order_by("id"))
        list(RefundIntent.objects.select_for_update().filter(pk__in=intent_ids).order_by("id"))
        notice, _ = WechatRefundNotice.objects.get_or_create(merchant_id=merchant_id, event_id=event_id,
            defaults={"intent": intent, "digest": digest, "provider_status": status})
        conflict = notice.intent_id != intent.id or notice.digest != digest
        if conflict and not notice.conflict:
            notice.conflict = True
            notice.save(update_fields=["conflict"])
        if conflict:
            for target in {intent.id, notice.intent_id}:
                WechatRefundConflict.objects.get_or_create(notice=notice, intent_id=target)
    if conflict or notice.conflict:
        raise RefundError("微信退款通知标识冲突，须人工核查。", "WECHAT_REFUND_EVENT_CONFLICT", 409)
    return notice


def _notice_state(intent, status, notice):
    with transaction.atomic():
        Order.objects.select_for_update().get(pk=intent.receipt.order_id)
        row = RefundIntent.objects.select_for_update().get(pk=intent.pk)
        notice = WechatRefundNotice.objects.select_for_update().get(pk=notice.pk)
        if notice.processed_at:
            return row.status
        notice.processed_at = timezone.now()
        notice.save(update_fields=["processed_at"])
        if row.status == "SUCCEEDED" or RefundEvidence.objects.filter(intent=row).exists():
            return row.status
        row.status = "FAILED" if status == "CLOSED" else "UNKNOWN"
        row.active_operation_id, row.lease_until = None, None
        row.save(update_fields=["status", "active_operation_id", "lease_until"])
        RefundHistory.objects.create(intent=row, action="WECHAT_" + status)
        return row.status


def handle_refund_notification(headers, raw):
    _outside()
    # Late signed notifications still settle when dispatch has subsequently been disabled.
    gateway = get_gateway()
    event_id, payload = gateway.verify_refund_notification(headers, raw)
    row = RefundIntent.objects.filter(refund_no=payload.get("out_refund_no")).first()
    if row is None:
        raise RefundError("微信退款业务号不存在，请核查。", "REFUND_NOT_FOUND", 404)
    intent = _intent(row.id)
    status = _validate(payload, gateway, intent, notification=True)
    notice = _notice(intent, gateway.config.merchant_id, event_id, payload, status)
    _blocked(intent)
    if status == "SUCCESS":
        return _accept(payload, gateway, intent, event_id)
    return {"outcome": _notice_state(intent, status, notice)}


def refund_data(case_id):
    intent = RefundIntent.objects.filter(case_id=case_id, channel="WECHAT").first()
    available, code = False, "WECHAT_REFUND_DISABLED"
    if getattr(settings, "WECHAT_REFUND_ENABLED", False):
        try:
            get_gateway()
            available, code = True, ""
        except WechatGatewayError as exc:
            code = exc.code
    case = AfterSaleCase.objects.get(pk=case_id)
    operation = intent.operations.order_by("-started_at", "-id").first() if intent else None
    failure = operation.failure_code if operation else code
    busy = bool(intent and intent.lease_until and intent.lease_until > timezone.now())
    evidence = bool(intent and intent.evidence.exists())
    blocked = bool(intent and intent.wechat_conflicts.exists())
    if blocked:
        failure = "WECHAT_REFUND_EVENT_CONFLICT"
    closed = bool(intent and (intent.wechat_notices.filter(provider_status="CLOSED").exists() or
                              intent.operations.filter(failure_code="WECHAT_REFUND_CLOSED").exists()))
    return {"available": available, "status": intent.status if intent else None,
        "refundNo": intent.refund_no if intent else None, "failureCode": failure or code,
        "canDispatch": available and case.status == "WAITING_REFUND" and not busy and not evidence and not closed and not blocked and
                       (not intent or intent.status in {"PREPARED", "FAILED"}),
        "canQuery": available and bool(intent) and intent.status != "SUCCEEDED" and not busy and not blocked}


def wechat_refund_data(case, actor):
    live = AdminAccount.objects.filter(pk=getattr(actor, "pk", None), enabled=True).first()
    if not live or not {"refund.prepare", "refund.offline.confirm"}.intersection(permissions(live)):
        return None
    data = refund_data(case.id)
    if "refund.prepare" not in permissions(live):
        return {**data, "canDispatch": False, "canQuery": False}
    return data


def reconcile_wechat_refunds(batch_size=100):
    _outside()
    if type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise RefundError("微信退款核查批次须为 1 至 500。")
    if not getattr(settings, "WECHAT_REFUND_ENABLED", False):
        return {"checked": 0, "succeeded": 0, "pending": 0, "failed": 0, "skipped": 1}
    gateway = _enabled_gateway()
    cutoff = timezone.now() - timedelta(minutes=1)
    recent = RefundOperation.objects.filter(started_at__gt=cutoff).values("intent_id")
    rows = list(RefundIntent.objects.filter(channel="WECHAT", status__in=["PROCESSING", "UNKNOWN"])
        .exclude(pk__in=recent).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=timezone.now()))
        .order_by("created_at", "id").values_list("id", flat=True)[:batch_size])
    counts = {"checked": len(rows), "succeeded": 0, "pending": 0, "failed": 0}
    for intent_id in rows:
        try:
            result = _perform(_intent(intent_id), gateway, "QUERY")
            name = "succeeded" if result["status"] == "SUCCEEDED" else "pending"
        except (RefundError, WechatGatewayError):
            name = "failed"
        counts[name] += 1
    return counts
