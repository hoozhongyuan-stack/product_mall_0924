from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as utc
from threading import Barrier

from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase

from business_numbers.models import BusinessNumberCounter
from business_numbers.service import next_number


INSTANT = datetime(2026, 10, 12, 6, 35, 26, tzinfo=utc.utc)


class NumberTests(TestCase):
    def setUp(self):
        # Historical-number migrations may seed counters; isolate generator cases.
        BusinessNumberCounter.objects.all().delete()

    def test_type_shanghai_time_and_daily_sequence(self):
        self.assertEqual(next_number('JH', INSTANT), 'JH20261012143526000001')
        self.assertEqual(next_number('JH', INSTANT), 'JH20261012143526000002')
        self.assertEqual(next_number('CY', INSTANT), 'CY20261012143526000001')
        tomorrow = datetime(2026, 10, 12, 16, tzinfo=utc.utc)
        self.assertEqual(next_number('JH', tomorrow), 'JH20261013000000000001')

    def test_rolled_back_business_creation_does_not_consume_counter(self):
        with self.assertRaises(ValueError):
            with transaction.atomic():
                next_number('XS', INSTANT)
                raise ValueError('business rejected')
        self.assertEqual(next_number('XS', INSTANT), 'XS20261012143526000001')

    def test_overflow_fails_without_reusing_existing_number(self):
        BusinessNumberCounter.objects.create(kind='TK', day=INSTANT.date(), sequence=999999)
        with self.assertRaisesMessage(ValueError, '当日编号已达上限'):
            next_number('TK', INSTANT)
        self.assertEqual(BusinessNumberCounter.objects.get(kind='TK').sequence, 999999)

    def test_reject_unknown_type_and_naive_time_before_write(self):
        for kind in ['XX', 'xs', None, "XS');DROP"]:
            with self.assertRaises(ValueError):
                next_number(kind, INSTANT)
        with self.assertRaises(ValueError):
            next_number('XS', datetime(2026, 10, 12))
        self.assertEqual(BusinessNumberCounter.objects.count(), 0)


class NumberConcurrencyTests(TransactionTestCase):
    def test_parallel_first_allocation_and_increment_are_unique(self):
        gate = Barrier(8)
        def allocate(_):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return next_number('XS', INSTANT)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=8) as pool:
            numbers = list(pool.map(allocate, range(8)))
        self.assertEqual(len(set(numbers)), 8)
        self.assertEqual(sorted(int(n[-6:]) for n in numbers), list(range(1, 9)))
