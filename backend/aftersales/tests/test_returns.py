"""D2 return acceptance precedes and is independent of synthetic funds."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections, transaction, DatabaseError
from django.test import TransactionTestCase, override_settings
from fulfillment.tests import test_flow as fixture
from fulfillment.models import Carrier
from fulfillment.service import ship_order
from aftersales.service import apply_case, review_case
from aftersales.models import AfterSaleAllocation, AfterSaleCase
from aftersales.returns import submit_return_shipment, preview_return_acceptance, accept_return, effective_refund
from inventory.models import InventoryBalance, InventoryLedger

@override_settings(WECHAT_MINI_APP_ID="wx-payment-test", ORDER_PAYMENT_METHODS_ENABLED={"WECHAT": True, "OFFLINE": True})
class ReturnFlowTests(TransactionTestCase):
    setUp = fixture.FulfillmentFlowTests.setUp
    _order = fixture.FulfillmentFlowTests._order
    _evidence = fixture.FulfillmentFlowTests._evidence
    _paid_order = fixture.FulfillmentFlowTests._paid_order

    def _case(self):
        order, line = self._paid_order("SHIP")
        Carrier.objects.get_or_create(code="D2", defaults={"name":"合成退货承运商", "enabled":True})
        ship_order(order.id, self.owner, "D2", "D2OUT123", order.revision, uuid.uuid4())
        c = apply_case(self.member, line.id, "RETURN_REFUND", 2, "合成退货退款申请", uuid.uuid4())
        return review_case(c.id, self.owner, True, "同意合成退货申请", c.revision)

    def _body(self, case, **changes):
        return {"expectedRevision":case.revision, "mode":"RECEIVED", "receivedQuantity":2,
            "salableQuantity":1, "refundQuantity":1, "refundAmountFen":None,
            "reason":"部分合成退货验收通过", **changes}

    def test_shipping_replay_and_immutable_history(self):
        c = self._case(); key = uuid.uuid4()
        b = {"expectedRevision":c.revision, "carrierName":"合成承运商", "trackingNo":"D2RETURN123"}
        row, replay = submit_return_shipment(c.id, self.member, b, key)
        again, replay = submit_return_shipment(c.id, self.member, b, key)
        self.assertTrue(replay); self.assertEqual(row.id, again.id)
        with self.assertRaises(ValueError):
            submit_return_shipment(c.id, self.member, {**b,"trackingNo":"OTHER"}, key)
        with self.assertRaises(DatabaseError), transaction.atomic():
            type(row).objects.filter(pk=row.id).update(tracking_no="EDIT")

    def test_preview_and_partial_acceptance_stock_before_funds(self):
        c = self._case(); b = self._body(c)
        before = InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units
        preview = preview_return_acceptance(c.id, self.owner, b)
        self.assertEqual(preview["refundAmountFen"], c.amount_fen // 2)
        self.assertEqual(InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units, before)
        row, replay = accept_return(c.id, self.owner, b, uuid.uuid4())
        c.refresh_from_db(); self.assertEqual(c.status, "WAITING_REFUND")
        self.assertEqual(effective_refund(c), (1,c.amount_fen//2))
        self.assertEqual(InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units, before+c.order_line.ratio)
        a = AfterSaleAllocation.objects.get(order_line=c.order_line)
        self.assertEqual((a.reserved_qty,a.reserved_fen),effective_refund(c))
        self.assertEqual(InventoryLedger.objects.filter(movement_type="RETURN").count(),1)
        from payments.models import RefundEvidence
        self.assertFalse(RefundEvidence.objects.exists())

    def test_zero_approval_releases_hold_and_waiver_never_restock(self):
        c = self._case(); before=InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units
        accept_return(c.id,self.owner,self._body(c, mode="WAIVED_RETURN",receivedQuantity=0,salableQuantity=0,refundQuantity=0),uuid.uuid4())
        c.refresh_from_db(); self.assertEqual(c.status,"REJECTED")
        self.assertEqual(AfterSaleAllocation.objects.get(order_line=c.order_line).reserved_qty,0)
        self.assertEqual(InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units,before)

    def test_quantity_money_and_permission_guards(self):
        c=self._case()
        for changes in ({"refundQuantity":True},{"receivedQuantity":3},{"salableQuantity":3},
                        {"receivedQuantity":0,"refundQuantity":1},{"refundAmountFen":c.amount_fen+1},
                        {"mode":"WAIVED_RETURN","receivedQuantity":1},{"reason":"短"}, {"expectedRevision":0}):
            with self.assertRaises(ValueError): preview_return_acceptance(c.id,self.owner,self._body(c,**changes))
        from accounts.models import AdminAccount
        AdminAccount.objects.filter(pk=self.owner.id).update(enabled=False)
        with self.assertRaises(ValueError): accept_return(c.id,self.owner,self._body(c),uuid.uuid4())

    def test_concurrent_same_acceptance_replays_once(self):
        c=self._case(); b=self._body(c); key=uuid.uuid4()
        def run(_):
            close_old_connections()
            try: return accept_return(c.id,self.owner,b,key)[0].id
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: ids=list(pool.map(run,range(2)))
        self.assertEqual(ids[0],ids[1]); self.assertEqual(InventoryLedger.objects.filter(movement_type="RETURN").count(),1)

    def test_acceptance_and_disposition_cannot_be_rewritten_or_fake_restocked(self):
        c=self._case(); row,_=accept_return(c.id,self.owner,self._body(c),uuid.uuid4())
        from inventory.models import ReturnDisposition
        for model,values in ((type(row),{"refund_amount_fen":1}),(ReturnDisposition,{"salable_quantity":0})):
            with self.assertRaises(DatabaseError),transaction.atomic(): model.objects.all().update(**values)
        with self.assertRaises(DatabaseError),transaction.atomic():
            InventoryLedger.objects.filter(return_case_id=c.id).update(operation_quantity=2)

    def test_adjusted_refund_uses_effective_values_and_never_restock_twice(self):
        c=self._case(); before=InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units
        row,_=accept_return(c.id,self.owner,self._body(c,refundAmountFen=500),uuid.uuid4())
        c.refresh_from_db()
        from payments.refunds import prepare_refund,VerifiedRefund,record_verified_refund
        from django.utils import timezone
        intent=prepare_refund(c.id,self.owner,uuid.uuid4())
        self.assertEqual(intent.amount_fen,500)
        from payments.models import PaymentReceipt
        receipt=PaymentReceipt.objects.get(order=c.order_line.order,applied_at__isnull=False)
        from accounts.models import AdminAccount
        checker=AdminAccount.objects.create_user(login_name="d2-independent-checker",password="Synthetic checker D2!",display_name="合成复核员")
        from accounts.models import PermissionGroup,GroupPermission
        group=PermissionGroup.objects.create(code="d2-checker",name="合成退款复核")
        GroupPermission.objects.create(group=group,code="refund.offline.confirm")
        checker.permission_groups.add(group)
        funds=VerifiedRefund(intent.refund_no,"OFFLINE",receipt.merchant_account_id,receipt.external_trade_no,"D2-REFUND-1",500,timezone.now(),"OFFLINE_RECONCILIATION",confirmed_by_id=checker.id)
        self.assertEqual(record_verified_refund(funds)["outcome"],"SUCCEEDED")
        self.assertEqual(record_verified_refund(funds)["outcome"],"SUCCEEDED")
        c.refresh_from_db(); self.assertEqual(c.status,"COMPLETED")
        alloc=AfterSaleAllocation.objects.get(order_line=c.order_line)
        self.assertEqual((alloc.refunded_qty,alloc.refunded_fen),(1,500))
        self.assertEqual(InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units,before+c.order_line.ratio)
        self.assertFalse(InventoryLedger.objects.filter(movement_type="REFUND").exists())

    def test_waived_return_approved_has_no_stock_effect(self):
        c=self._case(); before=InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units
        accept_return(c.id,self.owner,self._body(c,mode="WAIVED_RETURN",receivedQuantity=0,salableQuantity=0,refundQuantity=2),uuid.uuid4())
        c.refresh_from_db(); self.assertEqual(c.status,"WAITING_REFUND")
        self.assertEqual(effective_refund(c),(2,c.amount_fen))
        self.assertEqual(InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units,before)

    def test_rejected_received_units_cannot_be_stocked_again(self):
        c=self._case()
        accept_return(c.id,self.owner,self._body(c,receivedQuantity=2,salableQuantity=1,refundQuantity=0),uuid.uuid4())
        line=c.order_line
        next_case=apply_case(self.member,line.id,"RETURN_REFUND",2,"再次申请有数量上限",uuid.uuid4())
        next_case=review_case(next_case.id,self.owner,True,"同意重新审核申请",next_case.revision)
        with self.assertRaises(ValueError): accept_return(next_case.id,self.owner,self._body(next_case),uuid.uuid4())
        self.assertEqual(InventoryLedger.objects.filter(movement_type="RETURN").count(),1)

    def test_database_rejects_stock_acceptance_without_final_case_decision(self):
        from aftersales.returns import _decision,_digest
        from aftersales.models import ReturnAcceptance
        from aftersales.service import _locked_case
        from inventory.refunds import record_return_disposition_locked
        c=self._case(); body=self._body(c)
        before=InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units
        with self.assertRaises(DatabaseError),transaction.atomic():
            _,line,locked=_locked_case(c.id)
            decision=_decision(locked,body)
            row=ReturnAcceptance.objects.create(case=locked,actor=self.owner,request_key=uuid.uuid4(),request_digest=_digest(body),expected_revision=locked.revision,**decision)
            record_return_disposition_locked(line,row,self.owner)
            # Deliberately omit case/allocation final decision: direct SQL cannot accept this state.
        self.assertEqual(InventoryBalance.objects.get(sku=c.order_line.sku).on_hand_base_units,before)
        self.assertFalse(ReturnAcceptance.objects.exists())

    def test_different_concurrent_decisions_have_one_winner(self):
        c=self._case()
        def run(i):
            close_old_connections()
            try:
                return accept_return(c.id,self.owner,self._body(c,salableQuantity=i),uuid.uuid4())[0].id
            except ValueError: return None
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: values=list(pool.map(run,[0,1]))
        self.assertEqual(sum(v is not None for v in values),1)
