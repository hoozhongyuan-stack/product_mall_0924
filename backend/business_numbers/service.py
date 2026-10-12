"""Human-readable IDs; internal UUIDs and external payment references stay separate."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.utils import timezone


KINDS = frozenset(['XS', 'CY', 'JH', 'JF', 'SH', 'TK'])


@transaction.atomic
def next_number(kind, instant=None):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('业务编号类型无效。')
    instant = timezone.now() if instant is None else instant
    if not isinstance(instant, datetime) or timezone.is_naive(instant):
        raise ValueError('业务编号时间必须包含时区。')
    local = instant.astimezone(ZoneInfo('Asia/Shanghai'))
    # PostgreSQL serializes both concurrent first INSERTs and later increments.
    # Allocation is part of the caller's business transaction, including rollback.
    with connection.cursor() as cursor:
        cursor.execute('''
            INSERT INTO business_number_counter(kind, day, sequence) VALUES (%s, %s, 1)
            ON CONFLICT (kind, day) DO UPDATE
            SET sequence = business_number_counter.sequence + 1
            WHERE business_number_counter.sequence < 999999
            RETURNING sequence
        ''', [kind, local.date()])
        row = cursor.fetchone()
    if row is None:
        raise ValueError('当日编号已达上限，请联系平台。')
    return f'{kind}{local:%Y%m%d%H%M%S}{row[0]:06d}'


def procurement_number():
    return next_number('JH')


def aftersale_number():
    return next_number('SH')
