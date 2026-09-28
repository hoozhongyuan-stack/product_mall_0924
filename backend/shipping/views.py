"""Revisioned merchant shipping configuration."""

from django.db import transaction

from accounts.security import audit, error, parse_json, require, response
from accounts.views import method

from .models import ShippingPolicy


def _data(policy):
    return {"feeFen": policy.fee_fen, "deliveryScope": policy.delivery_scope,
            "revision": policy.revision}


def shipping_policy_view(request):
    bad = method(request, "GET", "PUT")
    if bad:
        return bad
    actor, denied = require(request, "settlement.shipping.manage")
    if denied:
        return denied
    if request.method == "GET":
        policy = ShippingPolicy.objects.filter(pk=1).first()
        if policy is None:
            return error(request, 503, "SHIPPING_UNAVAILABLE", "配送费配置不可用，请稍后重试。")
        return response(request, _data(policy))
    try:
        body = parse_json(request)
    except ValueError as exc:
        return error(request, 400, "VALIDATION_FAILED", str(exc))
    if set(body) != {"feeFen", "deliveryScope", "expectedRevision"}:
        return error(request, 400, "VALIDATION_FAILED", "运费配置字段不正确。")
    fee, scope, revision = body["feeFen"], body["deliveryScope"], body["expectedRevision"]
    if (type(fee) is not int or not 0 <= fee <= 1_000_000 or scope != "NATIONWIDE" or
            type(revision) is not int or revision < 1):
        return error(request, 400, "VALIDATION_FAILED", "运费须为 0 至 10000 元的整数分，配送范围须为全国。")
    with transaction.atomic():
        policy = ShippingPolicy.objects.select_for_update().filter(pk=1).first()
        if policy is None:
            return error(request, 503, "SHIPPING_UNAVAILABLE", "配送费配置不可用，请稍后重试。")
        if policy.revision != revision:
            return error(request, 409, "REVISION_CONFLICT", "运费配置已变化，请刷新后重试。",
                         [{"currentRevision": policy.revision}])
        if policy.fee_fen == fee and policy.delivery_scope == scope:
            return response(request, _data(policy))
        before = _data(policy)
        policy.fee_fen = fee
        policy.delivery_scope = scope
        policy.revision += 1
        policy.save(update_fields=["fee_fen", "delivery_scope", "revision", "updated_at"])
        audit(request, "shipping.policy.update", "shipping_policy", 1, actor,
              before=before, after=_data(policy))
        return response(request, _data(policy))
