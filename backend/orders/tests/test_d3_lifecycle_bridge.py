from unittest.mock import patch
from django.test import TestCase
from django.db import transaction


class D3LifecycleBridgeTests(TestCase):
    def test_pending_order_never_contributes(self):
        from orders.models import Order
        from orders.benefit_lifecycle import reconcile_benefits_locked
        order = Order(status='PENDING_PAYMENT')
        with transaction.atomic(), patch('benefits.lifecycle.reconcile_order_benefits_locked') as reconcile:
            self.assertEqual(reconcile_benefits_locked(order), {'outcome': 'UNPAID'})
            reconcile.assert_not_called()

    def test_bridge_requires_transaction(self):
        from orders.benefit_lifecycle import reconcile_benefits_locked
        # TestCase wraps each case, so use a connection mock for this guard.
        with patch('orders.benefit_lifecycle.connection') as connection:
            connection.in_atomic_block = False
            with self.assertRaises(RuntimeError):
                reconcile_benefits_locked(None)


from django.test import TransactionTestCase, override_settings
from fulfillment.tests import test_flow as fulfillment_fixture
from payments.tests import test_payment_flow as payment_fixture
from uuid import uuid4


@override_settings(WECHAT_MINI_APP_ID='wx-payment-test',
                   ORDER_PAYMENT_METHODS_ENABLED={'WECHAT': True, 'OFFLINE': True})
class D3FulfillmentIntegrationTests(TransactionTestCase):
    setUp = fulfillment_fixture.FulfillmentFlowTests.setUp
    _paid_order = fulfillment_fixture.FulfillmentFlowTests._paid_order
    _order = payment_fixture.PaymentFlowTests._order
    _evidence = payment_fixture.PaymentFlowTests._evidence

    def test_completion_reversal_and_recompletion_do_not_duplicate_rewards(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem, reverse_redemption
        from benefits.models import PointsAccount
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid4())
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)
        voucher.refresh_from_db()
        result = redeem(voucher.id, self.owner, 1, voucher.revision, uuid4())
        balance = PointsAccount.objects.get(member=self.member).settled_points
        self.assertEqual(balance, order.payable_fen // 100)
        voucher.refresh_from_db()
        reverse_redemption(result['eventId'], self.owner, '合成核销错误撤销', voucher.revision)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)
        voucher.refresh_from_db()
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid4())
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, balance)

    def test_active_aftersale_pauses_award_then_withdrawal_settles(self):
        from aftersales.service import apply_case, withdraw_case
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem
        from benefits.models import PointsAccount
        order, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid4())
        case = apply_case(self.member, line.id, 'REFUND_ONLY', 1, '合成已核销售后申请', uuid4(), 'USED')
        voucher.refresh_from_db()
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid4())
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)
        withdraw_case(case.id, self.member, case.revision)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, order.payable_fen // 100)
