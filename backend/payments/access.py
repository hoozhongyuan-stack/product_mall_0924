"""Read-only payment facts shared with after-sale transactions."""
from .models import PaymentReceipt


def applied_receipt(order):
    return PaymentReceipt.objects.filter(order=order, applied_at__isnull=False).first()
