"""Order-owned bridge: authoritative completion and refund facts into benefits."""
from django.db import connection


def reconcile_benefits_locked(order, *, source_ref=None):
    if not connection.in_atomic_block:
        raise RuntimeError('Order benefits require the surrounding order transaction')
    if order.status != 'PAID':
        return {'outcome': 'UNPAID'}
    from fulfillment.read import completion_fact
    from aftersales.read import benefit_refund_facts
    from payments.refund_amounts import shipping_refunded_fen
    from benefits.lifecycle import reconcile_order_benefits_locked
    facts = benefit_refund_facts(order.id)
    return reconcile_order_benefits_locked(
        order, fulfilled=completion_fact(order), active_aftersale=facts['active'],
        refunds=facts['refunds'], shipping_refunded_fen=shipping_refunded_fen(order.id),
        source_ref=source_ref)
