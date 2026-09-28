"""Named pricing entry used by checkout and orders."""

from .models import ShippingPolicy


class ShippingUnavailable(ValueError):
    pass


def current_shipping_policy(*, lock=False):
    queryset = ShippingPolicy.objects
    if lock:
        queryset = queryset.select_for_update()
    policy = queryset.filter(pk=1).first()
    if policy is None:
        raise ShippingUnavailable("配送费配置不可用，请稍后重试。")
    return policy


def shipping_fee_for_lines(lines, policy):
    if policy.delivery_scope != "NATIONWIDE":
        raise ShippingUnavailable("当前地址不在配送范围内。")
    return policy.fee_fen if any(line.get("fulfillmentKind") == "SHIP" for line in lines) else 0
