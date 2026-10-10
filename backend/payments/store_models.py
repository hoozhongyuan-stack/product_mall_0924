"""Store monetary facts owned by payments."""
import uuid
from django.db import models
from django.db.models import Q


class StoreOrderLineFinance(models.Model):
    line = models.OneToOneField('orders.OrderLine',primary_key=True,on_delete=models.PROTECT,related_name='store_finance')
    configured = models.BooleanField(default=False)
    purchase_cost_fen = models.PositiveBigIntegerField(null=True)
    platform_share_bps = models.PositiveSmallIntegerField(null=True)
    policy_revision = models.PositiveIntegerField(null=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=(Q(configured=False,purchase_cost_fen__isnull=True,platform_share_bps__isnull=True,policy_revision__isnull=True)|Q(configured=True,purchase_cost_fen__isnull=False,platform_share_bps__isnull=False,platform_share_bps__lte=10000,policy_revision__isnull=False,policy_revision__gte=1)),name='store_order_finance_snapshot_valid')]


class StoreWallet(models.Model):
    store = models.OneToOneField('stores.Store',primary_key=True,on_delete=models.PROTECT,related_name='wallet')
    available_fen = models.PositiveBigIntegerField(default=0)
    frozen_fen = models.PositiveBigIntegerField(default=0)
    paid_fen = models.PositiveBigIntegerField(default=0)
    revision = models.PositiveIntegerField(default=1)


class StoreIncome(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    order = models.OneToOneField('orders.Order',on_delete=models.PROTECT,related_name='store_income')
    store = models.ForeignKey('stores.Store',on_delete=models.PROTECT)
    paid_fen = models.PositiveBigIntegerField(default=0)
    cost_fen = models.PositiveBigIntegerField(default=0)
    platform_fen = models.PositiveBigIntegerField(default=0)
    freight_fen = models.PositiveBigIntegerField(default=0)
    store_fen = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=10,default='PENDING')
    reason = models.CharField(max_length=300,blank=True)
    available_at = models.DateTimeField(null=True)
    settled_at = models.DateTimeField(null=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(status__in=['PENDING','SETTLED','HELD']),name='store_income_status_valid')]


class StoreWithdrawal(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    store = models.ForeignKey('stores.Store',on_delete=models.PROTECT,related_name='withdrawals')
    member = models.ForeignKey('customers.Member',on_delete=models.PROTECT)
    request_key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    amount_fen = models.PositiveBigIntegerField()
    payee_name = models.CharField(max_length=80)
    bank_name = models.CharField(max_length=120)
    encrypted_bank_account = models.TextField()
    bank_account_tail = models.CharField(max_length=4)
    status = models.CharField(max_length=24,default='PENDING_REVIEW')
    revision = models.PositiveIntegerField(default=1)
    reason = models.CharField(max_length=300,blank=True)
    payment_reference = models.CharField(max_length=120,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['store','request_key'],name='store_withdrawal_request_unique'),models.CheckConstraint(condition=Q(amount_fen__gt=0),name='store_withdrawal_positive'),models.CheckConstraint(condition=Q(status__in=['PENDING_REVIEW','APPROVED_PENDING_PAYMENT','PAID','REJECTED']),name='store_withdrawal_status_valid')]


class StoreWalletEvent(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    store = models.ForeignKey('stores.Store',on_delete=models.PROTECT)
    kind = models.CharField(max_length=16)
    amount_fen = models.PositiveBigIntegerField()
    income = models.ForeignKey(StoreIncome,null=True,on_delete=models.PROTECT)
    withdrawal = models.ForeignKey(StoreWithdrawal,null=True,on_delete=models.PROTECT)
    actor_admin = models.ForeignKey('accounts.AdminAccount',null=True,on_delete=models.PROTECT)
    actor_member = models.ForeignKey('customers.Member',null=True,on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=(Q(kind='SETTLE',income__isnull=False,withdrawal__isnull=True)|Q(kind__in=['FREEZE','RELEASE','OFFLINE_PAY'],income__isnull=True,withdrawal__isnull=False)),name='store_wallet_event_source_valid'),models.UniqueConstraint(fields=['income','kind'],name='store_income_event_once'),models.UniqueConstraint(fields=['withdrawal','kind'],name='store_withdrawal_event_once')]


class StoreWithdrawalEvent(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    withdrawal = models.ForeignKey(StoreWithdrawal,on_delete=models.PROTECT,related_name='events')
    action = models.CharField(max_length=10)
    actor = models.ForeignKey('accounts.AdminAccount',null=True,on_delete=models.PROTECT)
    reason = models.CharField(max_length=300,blank=True)
    payment_reference = models.CharField(max_length=120,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['withdrawal','action'],name='store_withdrawal_action_once')]


class StoreSettlementCursor(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True,default=1,editable=False)
    last_created_at = models.DateTimeField(null=True)
    last_order_id = models.UUIDField(null=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(id=1),name='store_settlement_cursor_singleton')]
