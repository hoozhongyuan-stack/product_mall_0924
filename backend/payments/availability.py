"""The only server-side source for method-specific order submission gates."""

from django.conf import settings


METHODS = ("WECHAT", "OFFLINE")


def enabled_payment_methods(policy=None):
    configured = getattr(settings, "ORDER_PAYMENT_METHODS_ENABLED", {})
    if not isinstance(configured, dict):
        configured = {}
    if policy is None:
        from .models import OfflinePaymentPolicy
        policy = OfflinePaymentPolicy.objects.filter(pk=1).first()
    flags = {method: getattr(policy, method.lower() + "_enabled", None) for method in METHODS}
    return [method for method in METHODS if
            (configured.get(method) is True if flags[method] is None else flags[method] is True)]


def payment_method_enabled(method):
    return method in enabled_payment_methods()
