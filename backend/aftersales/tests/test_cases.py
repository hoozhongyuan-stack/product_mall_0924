"""D0 synthetic money tests against PostgreSQL."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections, transaction, DatabaseError
from django.test import TransactionTestCase, override_settings
from fulfillment.tests import test_flow as fixture
from aftersales.service import apply_case, review_case, withdraw_case, snapshot_order_policy_locked
from aftersales.models import AfterSaleAllocation, AfterSaleCase, AfterSaleEvent

@override_settings(WECHAT_MINI_APP_ID="wx-payment-test", ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class AfterSaleCasesTests(TransactionTestCase):
    setUp = fixture.FulfillmentFlowTests.setUp
    _order = fixture.FulfillmentFlowTests._order
    _evidence = fixture.FulfillmentFlowTests._evidence
    _paid_order = fixture.FulfillmentFlowTests._paid_order

    def test_apply_replay_reject_withdraw_release(self):
        order, line = self._paid_order()
        key = uuid.uuid4()
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", key)
        self.assertEqual(apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", key).id, case.id)
        with self.assertRaises(ValueError):
            apply_case(self.member, line.id, "REFUND_ONLY", 2, "未使用申请退款", key)
        review_case(case.id, self.owner, False, "申请原因需补充", case.revision)
        allocation = AfterSaleAllocation.objects.get(order_line=line)
        self.assertEqual((allocation.reserved_qty, allocation.reserved_fen), (0, 0))
        second = apply_case(self.member, line.id, "REFUND_ONLY", 2, "未使用申请退款", uuid.uuid4())
        withdraw_case(second.id, self.member, second.revision)
        allocation.refresh_from_db()
        self.assertEqual(allocation.reserved_qty, 0)
        self.assertEqual(AfterSaleEvent.objects.count(), 4)

    def test_concurrent_apply_cannot_over_allocate(self):
        _, line = self._paid_order()
        def run(_):
            close_old_connections()
            try:
                return apply_case(self.member, line.id, "REFUND_ONLY", 2, "并发申请退款", uuid.uuid4()).id
            except ValueError:
                return None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sum(row is not None for row in results), 1)
        self.assertEqual(AfterSaleAllocation.objects.get(order_line=line).reserved_qty, 2)

    def test_missing_snapshot_fails_closed_and_audit_immutable(self):
        from aftersales.models import OrderAfterSaleSnapshot
        order, line = self._paid_order()
        with self.assertRaises(DatabaseError), transaction.atomic():
            OrderAfterSaleSnapshot.objects.filter(order=order).delete()
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        with self.assertRaises(DatabaseError), transaction.atomic():
            AfterSaleEvent.objects.filter(case=case).update(reason="修改历史")
        with self.assertRaises(DatabaseError), transaction.atomic():
            AfterSaleAllocation.objects.filter(order_line=line).update(reserved_qty=3)

    def test_review_approval_holds_and_blocks_withdrawal(self):
        _, line = self._paid_order()
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        approved = review_case(case.id, self.owner, True, "同意未使用退款", case.revision)
        self.assertEqual(approved.status, "WAITING_REFUND")
        with self.assertRaises(ValueError):
            withdraw_case(case.id, self.member, approved.revision)
        self.assertEqual(AfterSaleAllocation.objects.get(order_line=line).reserved_qty, 1)
        with self.assertRaises(ValueError):
            review_case(case.id, self.owner, False, "重复审批应被拒绝", case.revision)

    def test_ownership_permissions_and_invalid_inputs(self):
        from customers.models import Member
        from accounts.models import AdminAccount
        other = Member.objects.create(wechat_app_id="wx-payment-test", wechat_openid="d0-other", grade=self.member.grade)
        staff = AdminAccount.objects.create_user(login_name="d0-staff", password="SyntheticD0!234", display_name="测试操作员")
        _, line = self._paid_order()
        for args in [("BAD", 1, "申请原因测试", uuid.uuid4()), ("REFUND_ONLY", True, "申请原因测试", uuid.uuid4()),
                     ("REFUND_ONLY", 1, "短", uuid.uuid4()), ("REFUND_ONLY", 1, "申请原因测试", "bad")]:
            with self.assertRaises(ValueError):
                apply_case(self.member, line.id, *args)
        with self.assertRaises(ValueError):
            apply_case(other, line.id, "REFUND_ONLY", 1, "申请原因测试", uuid.uuid4())
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "申请原因测试", uuid.uuid4())
        with self.assertRaises(ValueError):
            withdraw_case(case.id, other, case.revision)
        with self.assertRaises(ValueError):
            review_case(case.id, staff, True, "同意退款申请", case.revision)
        with self.assertRaises(ValueError):
            review_case(case.id, self.owner, "yes", "同意退款申请", case.revision)

    def test_shipping_kind_receipt_deadline_and_return_hold(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from fulfillment.models import Carrier, Shipment
        from fulfillment.service import ship_order, confirm_receipt
        from aftersales.service import finish_refund_locked
        order, line = self._paid_order("SHIP")
        with self.assertRaises(ValueError):
            apply_case(self.member, line.id, "RETURN_REFUND", 1, "退货退款申请", uuid.uuid4())
        Carrier.objects.create(code="D0", name="合成承运商", enabled=True)
        ship_order(order.id, self.owner, "D0", "D0TRACK123", order.revision, uuid.uuid4())
        with self.assertRaises(ValueError):
            apply_case(self.member, line.id, "REFUND_ONLY", 1, "仅退款申请测试", uuid.uuid4())
        confirm_receipt(self.member, order.id)
        confirmed = Shipment.objects.get(order=order).confirmed_at
        with patch("aftersales.service.timezone.now", return_value=confirmed + timedelta(days=16)):
            with self.assertRaises(ValueError):
                apply_case(self.member, line.id, "RETURN_REFUND", 1, "退货退款申请", uuid.uuid4())
        case = apply_case(self.member, line.id, "RETURN_REFUND", 1, "退货退款申请", uuid.uuid4())
        case = review_case(case.id, self.owner, True, "同意退货申请", case.revision)
        self.assertEqual(case.status, "WAITING_RETURN")
        with self.assertRaises(ValueError), transaction.atomic():
            finish_refund_locked(case)

    def test_used_and_unused_quantity_pools(self):
        from fulfillment.models import RedeemVoucher
        from fulfillment.service import redeem
        _, line = self._paid_order()
        voucher = RedeemVoucher.objects.get(order_line=line)
        redeem(voucher.id, self.owner, 1, voucher.revision, uuid.uuid4())
        with self.assertRaises(ValueError):
            apply_case(self.member, line.id, "REFUND_ONLY", 2, "未使用申请退款", uuid.uuid4())
        used = apply_case(self.member, line.id, "REFUND_ONLY", 1, "已使用人工申请", uuid.uuid4(), redemption_scope="USED")
        self.assertEqual((used.used_quantity, used.unredeemed_quantity), (1,0))
        withdraw_case(used.id, self.member, used.revision)
        unused = apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        self.assertEqual((unused.used_quantity, unused.unredeemed_quantity), (0,1))

    def test_expired_voucher_and_unpaid_and_missing_snapshot_rejected(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from aftersales.models import OrderAfterSaleSnapshot
        order, line = self._paid_order()
        with patch("aftersales.service.timezone.localdate", return_value=timezone.localdate()+timedelta(days=8)):
            with self.assertRaises(ValueError):
                apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        with patch("aftersales.service.OrderAfterSaleSnapshot.objects.filter") as snapshots:
            snapshots.return_value.first.return_value = None
            with self.assertRaises(ValueError):
                apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        with patch("payments.access.applied_receipt", return_value=None):
            with self.assertRaises(ValueError):
                apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        self.assertFalse(AfterSaleCase.objects.exists())

    def test_aggregate_and_request_immutability_guards(self):
        _, line = self._paid_order()
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "申请原因测试", uuid.uuid4())
        with self.assertRaises(DatabaseError), transaction.atomic():
            AfterSaleCase.objects.filter(pk=case.id).update(quantity=2)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AfterSaleAllocation.objects.filter(order_line=line).update(reserved_qty=0)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AfterSaleAllocation.objects.filter(order_line=line).update(payable_fen=line.payable_fen+1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AfterSaleCase.objects.filter(pk=case.id).update(status="COMPLETED")

    def test_same_key_cross_order_conflict(self):
        _, first = self._paid_order()
        _, second = self._paid_order()
        key = uuid.uuid4()
        apply_case(self.member, first.id, "REFUND_ONLY", 1, "申请原因测试", key)
        with self.assertRaises(ValueError):
            apply_case(self.member, second.id, "REFUND_ONLY", 1, "申请原因测试", key)

    def test_public_guards_and_batch_summaries(self):
        from aftersales.guards import assert_shippable_locked, assert_reversal_allowed_locked, reserved_unredeemed_quantity
        from aftersales.read import summaries, line_summaries
        order, line = self._paid_order()
        apply_case(self.member, line.id, "REFUND_ONLY", 1, "申请原因测试", uuid.uuid4())
        self.assertEqual(reserved_unredeemed_quantity(line.id), 1)
        self.assertEqual(summaries([order.id])[order.id]["activeCount"], 1)
        self.assertEqual(line_summaries([line.id])[line.id]["reservedUnredeemedQuantity"], 1)
        with transaction.atomic():
            assert_reversal_allowed_locked(line)
            assert_shippable_locked(order, [line])
        with self.assertRaises(RuntimeError):
            assert_reversal_allowed_locked(line)

    def test_discounted_fen_rounding_and_rejection_releases_exact_amount(self):
        from datetime import timedelta
        from django.utils import timezone
        from benefits.models import CouponCampaign, MemberCoupon
        from payments.service import record_verified_payment
        from orders.models import OrderLine
        now = timezone.now()
        campaign = CouponCampaign.objects.create(code="D0-ROUND", title="合成一分优惠券", kind="CASH", discount_fen=1,
            redeem_eligible=True, valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1))
        coupon = MemberCoupon.objects.create(member=self.member, campaign=campaign)
        order = self._order(coupon_id=coupon.id)
        record_verified_payment(self._evidence(order))
        line = OrderLine.objects.get(order=order)
        self.assertEqual(line.payable_fen, 1999)
        half = apply_case(self.member, line.id, "REFUND_ONLY", 1, "按成交价部分退款", uuid.uuid4())
        self.assertEqual(half.amount_fen, 999)
        review_case(half.id, self.owner, False, "拒绝后重新申请", half.revision)
        full = apply_case(self.member, line.id, "REFUND_ONLY", 2, "按成交价全部退款", uuid.uuid4())
        self.assertEqual(full.amount_fen, 1999)
        self.assertEqual(AfterSaleAllocation.objects.get(order_line=line).reserved_fen, 1999)

    def test_live_permission_revocation_and_order_revision(self):
        from accounts.models import AdminAccount
        order, line = self._paid_order()
        before = order.revision
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        order.refresh_from_db()
        self.assertEqual(order.revision, before+1)
        AdminAccount.objects.filter(pk=self.owner.id).update(enabled=False)
        with self.assertRaises(ValueError):
            review_case(case.id, self.owner, True, "权限撤销不能审核", case.revision)
        withdraw_case(case.id, self.member, case.revision)
        order.refresh_from_db()
        self.assertEqual(order.revision, before+2)

    def test_missing_case_and_snapshot_outside_transaction(self):
        from aftersales.service import finish_refund_locked
        _, line = self._paid_order()
        with self.assertRaises(ValueError):
            withdraw_case(uuid.uuid4(), self.member, 1)
        with self.assertRaises(ValueError):
            review_case("invalid-id", self.owner, True, "同意退款申请", 1)
        with self.assertRaises(RuntimeError):
            snapshot_order_policy_locked(line.order)
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        with self.assertRaises(RuntimeError):
            finish_refund_locked(case)

    def test_disabled_member_is_denied_at_service_boundary(self):
        from customers.models import Member
        _, line = self._paid_order()
        Member.objects.filter(pk=self.member.id).update(enabled=False)
        with self.assertRaises(ValueError):
            apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        self.assertFalse(AfterSaleCase.objects.exists())

    def test_completion_without_applied_funds_is_rejected_by_database(self):
        _, line = self._paid_order()
        case = apply_case(self.member, line.id, "REFUND_ONLY", 1, "未使用申请退款", uuid.uuid4())
        case = review_case(case.id, self.owner, True, "同意未使用退款", case.revision)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AfterSaleAllocation.objects.filter(order_line=line).update(reserved_qty=0, reserved_fen=0,
                refunded_qty=case.quantity, refunded_fen=case.amount_fen)
            AfterSaleCase.objects.filter(pk=case.id).update(status="COMPLETED")
        case.refresh_from_db()
        self.assertEqual(case.status, "WAITING_REFUND")
        self.assertEqual(AfterSaleAllocation.objects.get(order_line=line).refunded_qty, 0)
