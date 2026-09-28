"""The only server-side source for method-specific order submission gates."""

from django.conf import settings


METHODS = ("WECHAT", "OFFLINE")


def enabled_payment_methods():
    configured = getattr(settings, "ORDER_PAYMENT_METHODS_ENABLED", {})
    if not isinstance(configured, dict):
        return []
    return [method for method in METHODS if configured.get(method) is True]


def payment_method_enabled(method):
    return method in enabled_payment_methods()
