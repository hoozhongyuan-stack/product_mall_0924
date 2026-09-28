"""Coupon and point settlement is owned by benefits, not checkout or orders."""

import threading
from datetime import timedelta
from uuid import uuid4

from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from catalog.models import MemberGrade
from customers.models import Member

from benefits.models import (CouponCampaign, CouponEvent, MemberCoupon, PointsEvent, PointsGrant,
                             PointsReservation, PointsAccount)
from benefits.service import (BenefitError, consume_benefits, grant_points, quote_benefits,
                              release_benefits, reserve_benefits)


def lines(*amounts, fulfillment="SHIP"):
    product_id = str(uuid4())
    return [{"productId": product_id, "skuId": str(uuid4()),
             "fulfillmentKind": fulfillment, "amountFen": amount} for amount in amounts]


class BenefitFlowTests(TestCase):
    def setUp(self):
        grade = MemberGrade.objects.get(code="normal")
        self.member = Member.objects.create(wechat_app_id="wx-benefit", wechat_openid="one", grade=grade)
        self.other = Member.objects.create(wechat_app_id="wx-benefit", wechat_openid="two", grade=grade)
        self.now = timezone.now()

    def test_new_member_has_single_zero_points_account(self):
        account = PointsAccount.objects.get(member=self.member)
        self.assertEqual((account.settled_points, account.frozen_points), (0, 0))
        self.member.save(update_fields=["updated_at"])
        self.assertEqual(PointsAccount.objects.filter(member=self.member).count(), 1)

    def test_frozen_account_balance_cannot_be_negative(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PointsAccount.objects.filter(member=self.member).update(frozen_points=-1)
        self.assertEqual(PointsAccount.objects.get(member=self.member).frozen_points, 0)

    def coupon(self, *, member=None, kind="CASH", min_goods_fen=0, discount_fen=250,
               product_ids=None, redeem_eligible=False, starts=None, ends=None):
        campaign = CouponCampaign.objects.create(
            code=uuid4().hex, title="测试券", kind=kind, min_goods_fen=min_goods_fen,
            discount_fen=discount_fen, product_ids=product_ids or [],
            redeem_eligible=redeem_eligible,
            valid_from=starts or self.now - timedelta(days=1),
            valid_until=ends or self.now + timedelta(days=1))
        return MemberCoupon.objects.create(member=member or self.member, campaign=campaign)

    def test_quote_allocates_one_coupon_then_points_to_goods_only(self):
        goods = lines(1001, 999)
        coupon = self.coupon(discount_fen=301)
        grant_points(self.member, 1000, self.now + timedelta(days=5), "test-grant-one")
        quote = quote_benefits(self.member, goods, str(coupon.id), 339)
        self.assertEqual(quote["couponDiscountFen"], 301)
        self.assertEqual(quote["pointsDiscountFen"], 339)
        self.assertEqual(quote["availablePoints"], 1000)
        self.assertEqual(quote["selectedCouponId"], str(coupon.id))
        self.assertEqual(quote["pointsToUse"], 339)
        self.assertEqual(sum(row["couponDiscountFen"] for row in quote["allocations"]), 301)
        self.assertEqual(sum(row["pointsDiscountFen"] for row in quote["allocations"]), 339)
        self.assertEqual(sum(row["payableFen"] for row in quote["allocations"]), 1360)
        self.assertEqual(quote["availableCoupons"], [{"id": str(coupon.id), "title": "测试券",
                                                        "discountFen": 301}])
        with self.assertRaises(BenefitError) as error:
            quote_benefits(self.member, goods, str(coupon.id), 340)
        self.assertEqual(error.exception.code, "POINTS_LIMIT_EXCEEDED")

    def test_full_reduction_threshold_scope_and_redeem_default(self):
        goods = lines(500, 1000)
        goods[1]["productId"] = str(uuid4())
        coupon = self.coupon(kind="FULL_REDUCTION", min_goods_fen=1000, discount_fen=300,
                             product_ids=[goods[0]["productId"]])
        with self.assertRaises(BenefitError) as error:
            quote_benefits(self.member, goods, str(coupon.id))
        self.assertEqual(error.exception.code, "COUPON_UNAVAILABLE")
        goods[0]["amountFen"] = 1000
        self.assertEqual(quote_benefits(self.member, goods, str(coupon.id))["couponDiscountFen"], 300)
        redeem = lines(1200, fulfillment="REDEEM")
        cash = self.coupon(discount_fen=200)
        self.assertEqual(quote_benefits(self.member, redeem)["availableCoupons"], [])
        with self.assertRaises(BenefitError):
            quote_benefits(self.member, redeem, str(cash.id))
        cash.campaign.redeem_eligible = True
        cash.campaign.save(update_fields=["redeem_eligible"])
        self.assertEqual(quote_benefits(self.member, redeem, str(cash.id))["couponDiscountFen"], 200)

    def test_coupon_ownership_expiry_and_bad_input(self):
        goods = lines(500)
        other_coupon = self.coupon(member=self.other)
        expired = self.coupon(ends=self.now - timedelta(seconds=1))
        for coupon in (other_coupon, expired):
            with self.assertRaises(BenefitError) as error:
                quote_benefits(self.member, goods, str(coupon.id))
            self.assertEqual(error.exception.code, "COUPON_UNAVAILABLE")
            self.assertEqual(error.exception.status, 409)
        with self.assertRaises(BenefitError) as error:
            quote_benefits(self.member, goods, points_to_use=True)
        self.assertEqual(error.exception.status, 400)
        with self.assertRaises(BenefitError):
            quote_benefits(self.member, [goods[0], goods[0]])

    def test_guest_gets_plain_quote_without_member_rights(self):
        goods = lines(250)
        quote = quote_benefits(None, goods)
        self.assertEqual(quote["availablePoints"], 0)
        self.assertEqual(quote["availableCoupons"], [])
        self.assertEqual(quote["allocations"][0]["payableFen"], 250)
        with self.assertRaises(BenefitError) as error:
            quote_benefits(None, goods, points_to_use=1)
        self.assertEqual(error.exception.code, "LOGIN_REQUIRED")

    def test_reservation_release_reuse_and_consume_are_idempotent(self):
        goods = lines(1200)
        coupon = self.coupon(discount_fen=200)
        first = grant_points(self.member, 150, self.now + timedelta(days=1), "grant-earlier")
        second = grant_points(self.member, 150, self.now + timedelta(days=2), "grant-later")
        self.assertEqual((PointsAccount.objects.get(member=self.member).settled_points,
                          PointsAccount.objects.get(member=self.member).frozen_points), (300, 0))
        first_order = uuid4()
        with transaction.atomic():
            result = reserve_benefits(self.member, first_order, goods, str(coupon.id), 200)
        self.assertEqual(result["pointsDiscountFen"], 200)
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.pk).status, "RESERVED")
        self.assertEqual(PointsGrant.objects.get(pk=first.pk).reserved_points, 150)
        self.assertEqual(PointsGrant.objects.get(pk=second.pk).reserved_points, 50)
        self.assertEqual(PointsAccount.objects.get(member=self.member).frozen_points, 200)
        self.assertEqual(quote_benefits(self.member, goods)["availablePoints"], 100)
        self.assertEqual(list(CouponEvent.objects.filter(order_id=first_order).values_list("kind", flat=True)),
                         ["RESERVE"])
        with transaction.atomic():
            self.assertTrue(release_benefits(first_order))
            self.assertFalse(release_benefits(first_order))
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.pk).status, "AVAILABLE")
        self.assertEqual(quote_benefits(self.member, goods)["availablePoints"], 300)
        self.assertEqual((PointsAccount.objects.get(member=self.member).settled_points,
                          PointsAccount.objects.get(member=self.member).frozen_points), (300, 0))
        self.assertEqual(set(CouponEvent.objects.filter(order_id=first_order).values_list("kind", flat=True)),
                         {"RESERVE", "RELEASE"})
        second_order = uuid4()
        with transaction.atomic():
            reserve_benefits(self.member, second_order, goods, str(coupon.id), 200)
            self.assertTrue(consume_benefits(second_order))
            self.assertFalse(consume_benefits(second_order))
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.pk).status, "USED")
        self.assertEqual(set(CouponEvent.objects.filter(order_id=second_order).values_list("kind", flat=True)),
                         {"RESERVE", "CONSUME"})
        self.assertEqual(PointsReservation.objects.filter(order_id=second_order, status="CONSUMED").count(), 2)
        self.assertEqual(quote_benefits(self.member, goods)["availablePoints"], 100)
        self.assertEqual((PointsAccount.objects.get(member=self.member).settled_points,
                          PointsAccount.objects.get(member=self.member).frozen_points), (100, 0))
        self.assertEqual(PointsEvent.objects.filter(order_id=second_order, kind="CONSUME").count(), 2)

    def test_caller_rollback_preserves_coupon_and_point_balances(self):
        goods = lines(1000)
        coupon = self.coupon(discount_fen=100)
        grant_points(self.member, 100, self.now + timedelta(days=5), "rollback-grant")
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                reserve_benefits(self.member, uuid4(), goods, str(coupon.id), 100)
                raise RuntimeError("simulate later stock failure")
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.pk).status, "AVAILABLE")
        self.assertEqual(CouponEvent.objects.count(), 0)
        self.assertEqual(PointsGrant.objects.get().available_points, 100)
        self.assertEqual((PointsAccount.objects.get(member=self.member).settled_points,
                          PointsAccount.objects.get(member=self.member).frozen_points), (100, 0))
        self.assertEqual(PointsReservation.objects.count(), 0)

    def test_expired_points_are_excluded_and_grant_reference_is_idempotent(self):
        grant = grant_points(self.member, 50, self.now - timedelta(days=1), "expired-grant")
        self.assertEqual(quote_benefits(self.member, lines(1000))["availablePoints"], 0)
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 50)
        self.assertEqual(grant_points(self.member, 50, self.now - timedelta(days=1), "expired-grant").id,
                         grant.id)
        with self.assertRaises(BenefitError):
            grant_points(self.member, 60, self.now + timedelta(days=1), "expired-grant")

    def test_release_after_point_expiry_does_not_restore_spendable_points(self):
        grant = grant_points(self.member, 100, self.now + timedelta(days=1), "expires-during-hold")
        order_id = uuid4()
        with transaction.atomic():
            reserve_benefits(self.member, order_id, lines(1000), points_to_use=100)
        grant.expires_at = self.now - timedelta(seconds=1)
        grant.save(update_fields=["expires_at"])
        with transaction.atomic():
            release_benefits(order_id)
        self.assertEqual(PointsGrant.objects.get(pk=grant.pk).available_points, 0)
        self.assertEqual(PointsGrant.objects.get(pk=grant.pk).consumed_points, 100)
        self.assertEqual(quote_benefits(self.member, lines(1000))["availablePoints"], 0)
        self.assertEqual((PointsAccount.objects.get(member=self.member).settled_points,
                          PointsAccount.objects.get(member=self.member).frozen_points), (0, 0))

    def test_signed_account_balance_limits_lots_and_new_grants_first_offset_debt(self):
        grant_points(self.member, 100, self.now + timedelta(days=1), "debt-old-lot")
        account = PointsAccount.objects.get(member=self.member)
        account.settled_points = -10  # Future refunds may create debt; test the read boundary.
        account.save(update_fields=["settled_points"])
        self.assertEqual(quote_benefits(self.member, lines(1000))["availablePoints"], 0)
        grant_points(self.member, 20, self.now + timedelta(days=1), "debt-new-lot")
        self.assertEqual(PointsAccount.objects.get(member=self.member).settled_points, 10)
        self.assertEqual(quote_benefits(self.member, lines(1000))["availablePoints"], 10)

    def test_points_event_is_database_immutable(self):
        grant_points(self.member, 100, self.now + timedelta(days=1), "audit-grant")
        event = PointsEvent.objects.get(kind="GRANT")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                PointsEvent.objects.filter(pk=event.pk).update(amount=999)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                PointsEvent.objects.filter(pk=event.pk).delete()
        self.assertEqual(PointsEvent.objects.get(pk=event.pk).amount, 100)

    def test_coupon_event_is_database_immutable(self):
        coupon = self.coupon(discount_fen=100)
        with transaction.atomic():
            reserve_benefits(self.member, uuid4(), lines(1000), str(coupon.id))
        event = CouponEvent.objects.get(kind="RESERVE")
        self.assertEqual(event.discount_fen, 100)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                CouponEvent.objects.filter(pk=event.pk).update(discount_fen=999)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                CouponEvent.objects.filter(pk=event.pk).delete()
        self.assertEqual(CouponEvent.objects.get(pk=event.pk).discount_fen, 100)


class BenefitConcurrencyTests(TransactionTestCase):
    def setUp(self):
        grade, _ = MemberGrade.objects.get_or_create(
            code="benefit-concurrent", defaults={"name": "并发会员", "rank": 97})
        self.member = Member.objects.create(wechat_app_id="wx-concurrent-benefit", wechat_openid="one",
                                            grade=grade)

    def test_two_orders_competing_for_coupon_and_points_only_one_wins(self):
        now = timezone.now()
        campaign = CouponCampaign.objects.create(code=uuid4().hex, title="并发券", kind="CASH",
                                                  min_goods_fen=0, discount_fen=100,
                                                  valid_from=now - timedelta(days=1),
                                                  valid_until=now + timedelta(days=1))
        coupon = MemberCoupon.objects.create(member=self.member, campaign=campaign)
        grant_points(self.member, 100, now + timedelta(days=2), "concurrent-grant")
        goods = lines(1000)
        gate = threading.Barrier(2)
        successes, failures = [], []

        def reserve():
            close_old_connections()
            try:
                gate.wait(timeout=5)
                with transaction.atomic():
                    reserve_benefits(Member.objects.get(pk=self.member.pk), uuid4(), goods,
                                     str(coupon.id), 100)
                successes.append(True)
            except Exception as exc:
                failures.append(exc)
            finally:
                connection.close()

        workers = [threading.Thread(target=reserve) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertTrue(all(not worker.is_alive() for worker in workers), "权益锁死锁")
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], BenefitError)
        self.assertEqual(MemberCoupon.objects.get(pk=coupon.pk).status, "RESERVED")
        self.assertEqual(PointsGrant.objects.get().reserved_points, 100)
        account = PointsAccount.objects.get(member=self.member)
        self.assertEqual((account.settled_points, account.frozen_points), (100, 100))

    def test_two_orders_competing_for_points_only_one_wins(self):
        grant_points(self.member, 100, timezone.now() + timedelta(days=1), "points-only-race")
        goods = lines(1000)
        gate = threading.Barrier(2)
        successes, failures = [], []

        def reserve():
            close_old_connections()
            try:
                gate.wait(timeout=5)
                with transaction.atomic():
                    reserve_benefits(Member.objects.get(pk=self.member.pk), uuid4(), goods,
                                     points_to_use=100)
                successes.append(True)
            except Exception as exc:
                failures.append(exc)
            finally:
                connection.close()

        workers = [threading.Thread(target=reserve) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertTrue(all(not worker.is_alive() for worker in workers), "积分锁死锁")
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], BenefitError)
        self.assertEqual(failures[0].code, "POINTS_UNAVAILABLE")
        self.assertEqual(PointsGrant.objects.get().reserved_points, 100)
        account = PointsAccount.objects.get(member=self.member)
        self.assertEqual((account.settled_points, account.frozen_points), (100, 100))
