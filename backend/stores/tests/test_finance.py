from django.test import TestCase
from stores.finance import calculate_split


class FinanceCalculationTests(TestCase):
    def test_confirmed_profit_example(self):
        self.assertEqual(calculate_split(10000,6000,2000,0),{'platformFen':800,'storeFen':9200,'costFen':6000,'profitFen':4000,'freightFen':0})
        self.assertEqual(calculate_split(10000,6000,2000,1000)['storeFen'],8200)

    def test_loss_and_invalid_input_are_held(self):
        from stores.access import StoreError
        for args in [(5000,6000,2000,0),(10000,6000,10001,0),(10000,6000,2000,-1),(10000,6000,2000,10000)]:
            with self.assertRaises(StoreError):calculate_split(*args)

    def test_rounding_conserves_money(self):
        split=calculate_split(101,60,3333,7)
        self.assertEqual(split['platformFen']+split['storeFen']+7,101)

    def test_configured_snapshot_requires_nonnull_money_and_revision(self):
        import uuid
        from django.db import IntegrityError,transaction
        from payments.models import StoreOrderLineFinance
        for missing in ['platform_share_bps','policy_revision']:
            fields={'line_id':uuid.uuid4(),'configured':True,'purchase_cost_fen':1,'platform_share_bps':2000,'policy_revision':1}
            fields[missing]=None
            with self.assertRaisesMessage(IntegrityError,'store_order_finance_snapshot_valid'),transaction.atomic():
                StoreOrderLineFinance.objects.create(**fields)


class WithdrawalTests(TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from cryptography.fernet import Fernet
        from django.test import override_settings
        from inventory.models import Warehouse
        from customers.models import Member
        from stores.models import Store,StoreStaff
        from payments.models import StoreWallet
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        path=Path(self.directory.name)/'key';path.write_bytes(Fernet.generate_key())
        setting=override_settings(MALL_WECHAT_CREDENTIAL_KEY_FILE=str(path));setting.enable();self.addCleanup(setting.disable)
        self.store=Store.objects.create(name='财务前置仓',warehouse=Warehouse.objects.create(code='FINANCE',name='前置仓'))
        from catalog.models import MemberGrade
        grade=MemberGrade.objects.first() or MemberGrade.objects.create(code='FINANCE',name='财务测试等级',rank=1)
        self.member=Member.objects.create(wechat_openid='finance',wechat_app_id='test',grade=grade)
        StoreStaff.objects.create(store=self.store,member=self.member,permissions=['accounts'])
        self.wallet=StoreWallet.objects.create(store=self.store,available_fen=9200)

    def body(self):
        import uuid
        return {'requestKey':str(uuid.uuid4()),'amountFen':5000,'payeeName':'测试店长','bankName':'测试银行','bankAccount':'6222000012345678'}

    def test_request_freezes_once_and_masks_account(self):
        from stores.withdrawals import request_withdrawal,withdrawal_data
        from payments.models import StoreWalletEvent
        body=self.body();row,created=request_withdrawal(self.store,self.member,body)
        again,repeated=request_withdrawal(self.store,self.member,body)
        self.assertTrue(created);self.assertFalse(repeated);self.assertEqual(row.pk,again.pk)
        self.wallet.refresh_from_db();self.assertEqual((self.wallet.available_fen,self.wallet.frozen_fen),(4200,5000))
        self.assertEqual(StoreWalletEvent.objects.count(),1)
        self.assertEqual(withdrawal_data(row)['bankAccount'],'****5678')
        self.assertNotIn(body['bankAccount'],row.encrypted_bank_account)

    def test_review_reject_returns_frozen_and_pay_requires_approved(self):
        from stores.withdrawals import request_withdrawal,change_withdrawal
        from stores.access import StoreError
        row,_=request_withdrawal(self.store,self.member,self.body())
        with self.assertRaises(StoreError):change_withdrawal(row,'PAY','ref','',None)
        change_withdrawal(row,'REJECT','','拒绝测试',None)
        self.wallet.refresh_from_db();self.assertEqual((self.wallet.available_fen,self.wallet.frozen_fen),(9200,0))

    def test_approval_keeps_funds_frozen_until_offline_registration(self):
        from stores.withdrawals import request_withdrawal,change_withdrawal
        from payments.models import StoreWalletEvent
        row,_=request_withdrawal(self.store,self.member,self.body())
        change_withdrawal(row,'APPROVE','','',None)
        self.wallet.refresh_from_db();self.assertEqual(self.wallet.frozen_fen,5000)
        change_withdrawal(row,'PAY','线下银行流水测试','已核实',None)
        self.wallet.refresh_from_db();self.assertEqual((self.wallet.available_fen,self.wallet.frozen_fen,self.wallet.paid_fen),(4200,0,5000))
        self.assertEqual(StoreWalletEvent.objects.count(),2)

    def test_request_conflict_insufficient_and_revoked_scope(self):
        from stores.withdrawals import request_withdrawal
        from stores.access import StoreError
        from stores.models import StoreStaff
        body=self.body();request_withdrawal(self.store,self.member,body)
        for changed in [{**body,'amountFen':1000},{**self.body(),'amountFen':10000},{**self.body(),'bankAccount':'x'}, {**self.body(),'amountFen':True}]:
            with self.assertRaises(StoreError):request_withdrawal(self.store,self.member,changed)
        StoreStaff.objects.filter(store=self.store).update(enabled=False)
        with self.assertRaises(StoreError):request_withdrawal(self.store,self.member,self.body())

    def test_financial_evidence_cannot_be_changed(self):
        from django.db import DatabaseError,transaction
        from stores.withdrawals import request_withdrawal,change_withdrawal
        from payments.models import StoreWalletEvent,StoreWithdrawal,StoreWithdrawalEvent
        row,_=request_withdrawal(self.store,self.member,self.body())
        with self.assertRaises(DatabaseError),transaction.atomic():StoreWalletEvent.objects.update(amount_fen=1)
        with self.assertRaises(DatabaseError),transaction.atomic():StoreWithdrawal.objects.filter(pk=row.pk).update(amount_fen=1)
        change_withdrawal(row,'APPROVE','','',None)
        with self.assertRaises(DatabaseError),transaction.atomic():StoreWithdrawalEvent.objects.update(reason='篡改')


class GrossOrderSplitTests(TestCase):
    def order(self,lines,freight):
        from types import SimpleNamespace
        class Lines:
            def select_related(self,*args):return self
            def order_by(self,*args):return lines
        return SimpleNamespace(lines=Lines(),shipping_fee_fen=freight,payable_fen=sum(line.payable_fen for line in lines)+freight)
    def line(self,paid,cost,ratio,identity=1,qty=1):
        from types import SimpleNamespace
        import uuid
        return SimpleNamespace(id=uuid.UUID(int=identity),sku_id=uuid.UUID(int=identity),payable_fen=paid,quantity=qty,store_finance=SimpleNamespace(configured=True,purchase_cost_fen=cost,platform_share_bps=ratio))
    def test_shipping_is_in_gross_profit_basis_then_deducted_from_store(self):
        from payments.store_finance import order_split
        result=order_split(self.order([self.line(9000,6000,2000)],1000))
        self.assertEqual((result['paid_fen'],result['platform_fen'],result['store_fen']),(10000,800,8200))
        result=order_split(self.order([self.line(10000,6000,2000)],1000))
        self.assertEqual((result['paid_fen'],result['platform_fen'],result['store_fen']),(11000,1000,9000))
    def test_mixed_sku_ratios_largest_remainder_and_floor_conserve_money(self):
        from payments.store_finance import order_split
        order=self.order([self.line(101,50,3333,1),self.line(100,60,1000,2)],3)
        result=order_split(order)
        # Freight 2 and 1; gross line bases 103 and 101, platform floors 17+4.
        self.assertEqual(result['platform_fen'],21)
        self.assertEqual(result['store_fen'],180)
        self.assertEqual(result['store_fen']+result['platform_fen']+result['freight_fen'],result['paid_fen'])


from django.test import TransactionTestCase
class WithdrawalConcurrencyTests(TransactionTestCase):
    setUp=WithdrawalTests.setUp
    body=WithdrawalTests.body
    def concurrently(self,operation):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        barrier=Barrier(2)
        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker) for _ in range(2)]
            return [future.result(timeout=20) for future in futures]
    def test_duplicate_request_concurrent_freezes_only_once(self):
        from stores.withdrawals import request_withdrawal
        from payments.models import StoreWalletEvent
        body=self.body()
        rows=self.concurrently(lambda:request_withdrawal(self.store,self.member,body))
        self.assertEqual(sum(created for _,created in rows),1)
        self.wallet.refresh_from_db();self.assertEqual((self.wallet.available_fen,self.wallet.frozen_fen),(4200,5000))
        self.assertEqual(StoreWalletEvent.objects.count(),1)
    def test_concurrent_offline_payment_cannot_pay_twice(self):
        from stores.withdrawals import request_withdrawal,change_withdrawal
        from payments.models import StoreWalletEvent,StoreWithdrawal
        from stores.access import StoreError
        row,_=request_withdrawal(self.store,self.member,self.body())
        change_withdrawal(row,'APPROVE','','',None)
        def pay():
            candidate=StoreWithdrawal.objects.get(pk=row.pk)
            try:return change_withdrawal(candidate,'PAY','SYNTHETIC-REF','测试',None).status
            except StoreError as exc:return exc.code
        statuses=self.concurrently(pay)
        self.assertEqual(statuses.count('PAID'),1)
        self.wallet.refresh_from_db();self.assertEqual(self.wallet.paid_fen,5000)
        self.assertEqual(StoreWalletEvent.objects.filter(kind='OFFLINE_PAY').count(),1)
