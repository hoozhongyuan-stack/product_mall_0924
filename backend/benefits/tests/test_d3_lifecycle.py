from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from catalog.models import MemberGrade
from customers.models import Member
from benefits.models import PointsAccount, PointsGrant, PointsReservation
from benefits.service import grant_points
from benefits.policy import current_policy
from benefits.lifecycle import (snapshot_order_benefits_locked,
                                reconcile_order_benefits_locked, expire_points)


class D3LifecycleTests(TestCase):
    def setUp(self):
        self.at = timezone.now()
        self.member = Member.objects.create(wechat_app_id='wx-d3', wechat_openid=uuid4().hex,
                                           grade=MemberGrade.objects.get(code='normal'))
        self.line = SimpleNamespace(id=uuid4(), quantity=2, payable_fen=50000,
                                    points_discount_fen=0, coupon_discount_fen=0)
        self.order = SimpleNamespace(id=uuid4(), member_id=self.member.id,
            member=self.member, payable_fen=50000, points_to_use=0,
            coupon_id=None, coupon_discount_fen=0, status='PAID',
            benefit_policy_snapshot=current_policy(),
            lines=SimpleNamespace(all=lambda: [self.line], order_by=lambda *fields: [self.line]))
        snapshot_order_benefits_locked(self.order)

    def sync(self, **kwargs):
        return reconcile_order_benefits_locked(self.order, fulfilled=True,
            active_aftersale=False, refunds=kwargs.pop('refunds', []), at=self.at, **kwargs)

    def test_complete_once_and_refund_downgrades(self):
        self.sync()
        self.sync()
        account = PointsAccount.objects.get(member=self.member)
        self.assertEqual(account.settled_points, 500)
        self.member.refresh_from_db()
        self.assertEqual(self.member.grade.code, 'silver')
        self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': 1, 'amountFen': 25000}])
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 250)
        self.member.refresh_from_db()
        self.assertEqual(self.member.grade.code, 'normal')

    def test_active_after_sale_and_unfulfilled_delay(self):
        reconcile_order_benefits_locked(self.order, fulfilled=False, active_aftersale=False,
                                       refunds=[], at=self.at)
        reconcile_order_benefits_locked(self.order, fulfilled=True, active_aftersale=True,
                                       refunds=[], at=self.at)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)
        self.sync()
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 500)

    def test_refund_spent_earned_points_creates_debt_and_grant_offsets(self):
        self.sync()
        grant = PointsGrant.objects.get(source_ref='earn:' + str(self.order.id))
        grant.available_points = 0
        grant.consumed_points = 500
        grant.save()
        PointsAccount.objects.filter(member=self.member).update(settled_points=0)
        self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': 1, 'amountFen': 25000}])
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, -250)
        fresh = grant_points(self.member, 300, self.at + timedelta(days=1), 'debt-topup')
        self.assertEqual(fresh.available_points, 50)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 50)

    def test_expiry_once_never_expires_frozen_points(self):
        grant = grant_points(self.member, 100, self.at - timedelta(seconds=1), 'expired')
        grant.available_points = 60
        grant.reserved_points = 40
        grant.save()
        PointsAccount.objects.filter(member=self.member).update(frozen_points=40)
        expire_points(self.member, at=self.at)
        expire_points(self.member, at=self.at)
        grant.refresh_from_db()
        self.assertEqual((grant.available_points, grant.reserved_points, grant.consumed_points), (0, 40, 60))
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 40)

    def test_expired_deduction_refund_has_short_lifetime_and_repeat_safe(self):
        self.line.points_discount_fen = 100
        self.order.points_to_use = 100
        from benefits.models import OrderBenefitSnapshot
        self.order.id = uuid4()
        snapshot_order_benefits_locked(self.order)
        grant = grant_points(self.member, 100, self.at - timedelta(days=1), 'used-expired')
        grant.available_points = 0
        grant.consumed_points = 100
        grant.save()
        PointsAccount.objects.filter(member=self.member).update(settled_points=0)
        PointsReservation.objects.create(member=self.member, grant=grant, order_id=self.order.id,
                                         amount=100, status='CONSUMED', resolved_at=self.at)
        refund = [{'lineId': str(self.line.id), 'quantity': 1, 'amountFen': 25000}]
        reconcile_order_benefits_locked(self.order, fulfilled=False, active_aftersale=False,
                                       refunds=refund, at=self.at)
        reconcile_order_benefits_locked(self.order, fulfilled=False, active_aftersale=False,
                                       refunds=refund, at=self.at)
        returned = PointsGrant.objects.get(source_ref__startswith='return:')
        self.assertEqual(returned.original_points, 50)
        self.assertEqual(returned.expires_at, self.at + timedelta(days=30))
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 50)

    def test_completed_small_cash_keeps_consumption_during_new_active_case(self):
        self.order.id = uuid4()
        self.order.payable_fen = 99
        self.line.payable_fen = 99
        snapshot_order_benefits_locked(self.order)
        self.sync()
        result = reconcile_order_benefits_locked(self.order, fulfilled=True,
            active_aftersale=True, refunds=[], at=self.at)
        self.assertEqual(result['effectiveSpendFen'], 99)
        self.assertEqual(result['earnedPoints'], 0)

    def test_reversal_and_recompletion_only_restore_difference(self):
        self.sync()
        reconcile_order_benefits_locked(self.order, fulfilled=False, active_aftersale=False,
            refunds=[], at=self.at)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)
        self.sync()
        self.sync()
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 500)

    def test_frozen_clawback_release_does_not_resurrect_points_or_double_expire(self):
        from benefits.service import release_benefits
        self.sync()
        grant = PointsGrant.objects.get(source_ref='earn:' + str(self.order.id))
        grant.available_points = 0
        grant.reserved_points = 500
        grant.save()
        PointsAccount.objects.filter(member=self.member).update(frozen_points=500)
        held_order_id = uuid4()
        PointsReservation.objects.create(member=self.member, grant=grant,
            order_id=held_order_id, amount=500, status='RESERVED')
        self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': 2, 'amountFen': 50000}])
        release_benefits(held_order_id)
        grant.refresh_from_db()
        self.assertEqual((grant.available_points, grant.reserved_points, grant.consumed_points), (0, 0, 500))
        expire_points(self.member, at=self.at + timedelta(days=366))
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)

    def test_multiple_original_lots_return_monotonically_with_original_expiry(self):
        from benefits.models import BenefitLedger
        self.order.id = uuid4()
        self.line.points_discount_fen = 3
        self.order.points_to_use = 3
        self.line.payable_fen = 3
        self.line.quantity = 3
        self.order.payable_fen = 3
        snapshot_order_benefits_locked(self.order)
        for index in range(3):
            grant = grant_points(self.member, 1, self.at + timedelta(days=index + 1), f'lot-{index}')
            grant.available_points = 0
            grant.consumed_points = 1
            grant.save()
            PointsReservation.objects.create(member=self.member, grant=grant,
                order_id=self.order.id, amount=1, status='CONSUMED')
        PointsAccount.objects.filter(member=self.member).update(settled_points=0)
        self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': 2, 'amountFen': 2}])
        self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': 3, 'amountFen': 3}])
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 3)
        self.assertEqual(list(BenefitLedger.objects.filter(kind='RETURN')
                             .values_list('amount', flat=True)), [1, 1, 1])

    def test_zero_cash_returns_by_quantity_and_tail_completes(self):
        self.order.id = uuid4()
        self.line.points_discount_fen = 5
        self.order.points_to_use = 5
        self.line.payable_fen = 0
        self.line.quantity = 3
        self.order.payable_fen = 0
        snapshot_order_benefits_locked(self.order)
        grant = grant_points(self.member, 5, self.at + timedelta(days=1), 'zero-deduct')
        grant.available_points = 0
        grant.consumed_points = 5
        grant.save()
        PointsAccount.objects.filter(member=self.member).update(settled_points=0)
        PointsReservation.objects.create(member=self.member, grant=grant, order_id=self.order.id,
            amount=5, status='CONSUMED')
        for qty, expected in [(1, 1), (2, 3), (3, 5)]:
            result = self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': qty, 'amountFen': 0}])
            self.assertEqual(result['returnedPoints'], expected)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 5)

    def test_missing_historical_snapshot_is_explicitly_skipped(self):
        self.order.id = uuid4()
        self.assertEqual(self.sync()['status'], 'SKIPPED')

    def test_refund_regression_and_malformed_summary_rejected(self):
        from benefits.service import BenefitError
        self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': 1, 'amountFen': 25000}])
        with self.assertRaises(BenefitError):
            self.sync(refunds=[])
        with self.assertRaises(BenefitError):
            self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': True, 'amountFen': 25000}])

    def test_snapshot_and_ledgers_are_database_immutable(self):
        from django.db import DatabaseError
        from benefits.models import BenefitLedger, OrderBenefitSnapshot
        self.sync()
        with self.assertRaises(DatabaseError), transaction.atomic():
            OrderBenefitSnapshot.objects.filter(order_id=self.order.id).update(earn_points=999)
        with self.assertRaises(DatabaseError), transaction.atomic():
            BenefitLedger.objects.filter(order_id=self.order.id).delete()

    def test_policy_change_only_affects_new_snapshots(self):
        from benefits.lifecycle import update_points_policy
        update_points_policy(expected_revision=1, earn_unit_fen=100, earn_points=2,
                             valid_days=30, refund_valid_days=3)
        self.sync()
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 500)
        self.order.id = uuid4()
        self.order.benefit_policy_snapshot = current_policy()
        snapshot_order_benefits_locked(self.order)
        self.sync()
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 1500)

    def test_full_goods_refund_with_retained_shipping_does_not_restore_coupon(self):
        from benefits.models import CouponCampaign, CouponEvent, MemberCoupon
        campaign = CouponCampaign.objects.create(code=uuid4().hex, title='券', kind='CASH',
            discount_fen=100, valid_from=self.at - timedelta(days=1),
            valid_until=self.at + timedelta(days=1))
        self.order.id = uuid4()
        self.order.payable_fen = 50500
        coupon = MemberCoupon.objects.create(member=self.member, campaign=campaign,
            status='USED', reserved_order_id=self.order.id, used_at=self.at)
        self.order.coupon_id = coupon.id
        CouponEvent.objects.create(coupon=coupon, member=self.member, order_id=self.order.id,
                                   kind='CONSUME', discount_fen=100)
        snapshot_order_benefits_locked(self.order)
        refund = [{'lineId': str(self.line.id), 'quantity': 2, 'amountFen': 50000}]
        self.assertFalse(self.sync(refunds=refund)['couponRestored'])
        self.assertTrue(self.sync(refunds=refund, shipping_refunded_fen=500)['couponRestored'])
        coupon.refresh_from_db()
        self.assertEqual(coupon.status, 'AVAILABLE')
        self.assertEqual(coupon.restored_valid_until, campaign.valid_until)
        campaign.valid_until = self.at + timedelta(days=100)
        campaign.save()
        from benefits.service import quote_benefits
        from benefits.tests.test_benefits import lines
        self.assertEqual(quote_benefits(self.member, lines(1000),
            at=self.at + timedelta(days=2))['availableCoupons'], [])

    def test_expired_reward_refund_does_not_debit_natural_expiry_twice(self):
        self.sync()
        expire_points(self.member, at=self.at + timedelta(days=366))
        reconcile_order_benefits_locked(self.order, fulfilled=True, active_aftersale=False,
            refunds=[{'lineId': str(self.line.id), 'quantity': 2, 'amountFen': 50000}],
            at=self.at + timedelta(days=367))
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 0)

    def test_ledger_cause_links_to_refund_case(self):
        from benefits.models import BenefitLedger
        self.sync()
        self.sync(refunds=[{'lineId': str(self.line.id), 'quantity': 1, 'amountFen': 25000}],
                  source_ref='case:synthetic-case')
        self.assertTrue(BenefitLedger.objects.filter(kind='CLAWBACK',
                                                    cause_ref='case:synthetic-case').exists())

    def test_normalization_queries_do_not_grow_per_historical_lot(self):
        from benefits.lifecycle import normalize_resolved_lots_locked
        grants = [PointsGrant.objects.create(member=self.member, source_ref=f'many-{index}',
            original_points=1, available_points=1, expires_at=self.at + timedelta(days=1))
            for index in range(20)]
        account = PointsAccount.objects.get(member=self.member)
        with self.assertNumQueries(2):
            normalize_resolved_lots_locked(account, grants, self.at)
