"""Real PostgreSQL races: member prelocks precede shared stock across domains."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from benefits.models import PointsAccount
from benefits.service import grant_points
from checkout.service import create_quote
from inventory.models import InventoryBalance, InventoryReservation
from orders.models import Order
from orders.service import cancel_order, submit_order
from payments.service import record_verified_payment
from . import test_payment_flow as payment_flow


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',
                   ORDER_PAYMENT_METHODS_ENABLED={'OFFLINE': True, 'WECHAT': True})
class D3LockOrderTests(TransactionTestCase):
    setUp = payment_flow.PaymentFlowTests.setUp
    _order = payment_flow.PaymentFlowTests._order
    _evidence = payment_flow.PaymentFlowTests._evidence

    def _race(self, action):
        grant_points(self.member, 200, timezone.now() + timedelta(days=2), 'D3-lock-race')
        old = self._order(points=100)
        quote = create_quote({'items': [{'skuId': str(self.sku.id), 'quantity': 2}]}, self.member)
        evidence = self._evidence(old, trade_no=uuid.uuid4().hex, event_id=uuid.uuid4().hex)
        start = Barrier(2)

        def worker(kind):
            close_old_connections()
            try:
                # Set limits before the start barrier, outside any business transaction.
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                start.wait(timeout=5)
                if kind == 'SUBMIT':
                    return submit_order(self.member, {'quoteId': quote['quoteId'],
                        'paymentMethod': 'OFFLINE'}, uuid.uuid4())[0]
                if action == 'PAY':
                    return record_verified_payment(evidence)
                return cancel_order(self.member, old.id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            submitted = executor.submit(worker, 'SUBMIT')
            resolved = executor.submit(worker, action)
            new_data = submitted.result(timeout=15)
            old_result = resolved.result(timeout=15)
        old.refresh_from_db()
        self.assertEqual(Order.objects.get(pk=new_data['orderId']).status, 'PENDING_PAYMENT')
        self.assertEqual(InventoryReservation.objects.filter(status='ACTIVE').count(), 1)
        balance = InventoryBalance.objects.get(pk=self.balance.pk)
        self.assertEqual(balance.reserved_base_units, 2)
        account = PointsAccount.objects.get(member=self.member)
        self.assertEqual(account.frozen_points, 0)
        if action == 'PAY':
            self.assertEqual(old.status, 'PAID')
            self.assertEqual(old_result['outcome'], 'PAID')
            self.assertEqual(balance.on_hand_base_units, 8)
            self.assertEqual(account.settled_points, 100)
        else:
            self.assertEqual(old.status, 'CLOSED')
            self.assertEqual(balance.on_hand_base_units, 10)
            self.assertEqual(account.settled_points, 200)

    def test_new_submission_and_old_payment_share_member_and_stock_without_deadlock(self):
        self._race('PAY')

    def test_new_submission_and_old_close_share_member_and_stock_without_deadlock(self):
        self._race('CLOSE')
