"""Named policy setting boundary; immutable order snapshots stay unchanged."""
from django.db import transaction
from .models import AfterSalePolicy
from .service import AfterSaleError


def current_policy(lock=False):
    query=AfterSalePolicy.objects.select_for_update() if lock else AfterSalePolicy.objects
    row=query.filter(pk=1).first()
    if row is None:
        raise AfterSaleError('售后期配置暂不可用，请检查数据库初始化。','POLICY_UNAVAILABLE',503)
    return row


def policy_data(row=None):
    row=current_policy() if row is None else row
    return {'receivedWindowDays':row.received_window_days,'revision':row.revision,'appliesTo':'NEW_ORDERS'}


def configure_policy_locked(row,days):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Policy changes require an atomic transaction')
    if type(days) is not int or not 1<=days<=365:
        raise AfterSaleError('售后期须为 1 至 365 天的整数。')
    row.received_window_days=days
    row.revision+=1
    row.save(update_fields=['received_window_days','revision'])
    return policy_data(row)
