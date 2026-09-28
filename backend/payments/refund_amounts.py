"""Public paid shipping refund fact for order benefits coordination."""
from aftersales.refund_amounts import completed_shipping_refund_fen


def shipping_refunded_fen(order_id):
    return completed_shipping_refund_fen(order_id)
