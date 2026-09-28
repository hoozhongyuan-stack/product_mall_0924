"""Draft-only subscription template APIs until a real platform is verified."""

import re
from datetime import timedelta
from math import ceil

from django.db import transaction
from django.utils import timezone

from accounts.models import AdminAccount, AuditLog
from accounts.security import audit, error, parse_json, require, require_live, response
from accounts.views import method

from .models import EVENT_TYPES, SubscriptionTemplateDraft


def _status(draft):
    if not draft.draft_app_id and not draft.draft_template_id:
        return "UNBOUND"
    if not draft.draft_app_id or not draft.draft_template_id:
        return "INCOMPLETE"
    return "DRAFT_UNVERIFIED"


def _data(event_type, draft=None):
    if draft is None:
        return {"eventType": event_type, "revision": 0, "draftAppId": "",
                "draftTemplateId": "", "status": "UNBOUND", "enabled": False}
    return {"eventType": event_type, "revision": draft.revision,
            "draftAppId": draft.draft_app_id, "draftTemplateId": draft.draft_template_id,
            "status": _status(draft), "enabled": False}


def template_drafts_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    _, bad = require(request, "notification.read")
    if bad:
        return bad
    drafts = {item.event_type: item for item in SubscriptionTemplateDraft.objects.all()}
    return response(request, {"events": [_data(event, drafts.get(event)) for event in EVENT_TYPES],
                              "sendingAvailable": False})


def _valid_identifier(value, max_length):
    return (isinstance(value, str) and len(value) <= max_length and
            re.fullmatch(r"[A-Za-z0-9_-]*", value) is not None)


def template_draft_view(request, event_type):
    bad = method(request, "PUT")
    if bad:
        return bad
    actor, bad = require(request, "notification.manage")
    if bad:
        return bad
    if event_type not in EVENT_TYPES:
        return error(request, 404, "NOT_FOUND", "消息事件不存在。")
    try:
        body = parse_json(request)
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    if (set(body) != {"expectedRevision", "draftAppId", "draftTemplateId"} or
            type(body["expectedRevision"]) is not int or body["expectedRevision"] < 0 or
            not _valid_identifier(body["draftAppId"], 64) or
            not _valid_identifier(body["draftTemplateId"], 128)):
        return error(request, 400, "VALIDATION_FAILED", "模板草稿参数不正确。")
    with transaction.atomic():
        draft = SubscriptionTemplateDraft.objects.select_for_update().filter(event_type=event_type).first()
        if draft is None:
            return error(request, 503, "DRAFT_UNAVAILABLE", "消息模板草稿暂不可用。")
        # Serialize this actor's writes across all three event rows.
        AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
        actor, bad = require_live(request, "notification.manage")
        if bad:
            return bad
        recent_writes = AuditLog.objects.filter(
            actor=actor, action_code="notification.template_draft.update",
            occurred_at__gte=timezone.now() - timedelta(hours=1),
        ).order_by("occurred_at")
        if recent_writes.count() >= 30:
            oldest = recent_writes.values_list("occurred_at", flat=True).first()
            retry_after = max(1, ceil((oldest + timedelta(hours=1) - timezone.now()).total_seconds()))
            limited = error(request, 429, "RATE_LIMITED", "过去一小时的消息草稿保存次数已达上限，请稍后再试。")
            limited["Retry-After"] = str(retry_after)
            return limited
        if draft.revision != body["expectedRevision"]:
            return error(request, 409, "REVISION_CONFLICT", "草稿已被修改，请刷新后重试。",
                         [{"currentRevision": draft.revision}])
        before = {"revision": draft.revision, "status": _status(draft)}
        draft.draft_app_id = body["draftAppId"]
        draft.draft_template_id = body["draftTemplateId"]
        draft.revision += 1
        draft.save(update_fields=["draft_app_id", "draft_template_id", "revision", "updated_at"])
        audit(request, "notification.template_draft.update", "subscription_template_draft", event_type,
              actor, before=before, after={"revision": draft.revision, "status": _status(draft)})
    return response(request, _data(event_type, draft))


def public_availability_view(request):
    bad = method(request, "GET")
    if bad:
        return bad
    return response(request, {"available": False, "reasonCode": "PLATFORM_NOT_VERIFIED",
                              "events": [{"eventType": event, "available": False} for event in EVENT_TYPES]})
