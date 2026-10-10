"""D0 quantity/money holds and immutable case history."""
import uuid
from django.db import models
from django.db.models import F, Q

class AfterSalePolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    received_window_days = models.PositiveSmallIntegerField(default=15)
    revision = models.PositiveIntegerField(default=1)
    class Meta:
        db_table = "aftersale_policy"
        constraints = [models.CheckConstraint(condition=Q(id=1), name="aftersale_policy_singleton"),
            models.CheckConstraint(condition=Q(received_window_days__gte=1, received_window_days__lte=365), name="aftersale_policy_days")]

class OrderAfterSaleSnapshot(models.Model):
    order = models.OneToOneField("orders.Order", primary_key=True, on_delete=models.PROTECT)
    received_window_days = models.PositiveSmallIntegerField()
    policy_revision = models.PositiveIntegerField()
    class Meta:
        db_table = "aftersale_order_snapshot"
        constraints = [models.CheckConstraint(condition=Q(received_window_days__gte=1, received_window_days__lte=365), name="aftersale_snapshot_days")]

class AfterSaleAllocation(models.Model):
    order_line = models.OneToOneField("orders.OrderLine", primary_key=True, on_delete=models.PROTECT)
    purchased_qty = models.PositiveIntegerField()
    payable_fen = models.PositiveBigIntegerField()
    reserved_qty = models.PositiveIntegerField(default=0)
    refunded_qty = models.PositiveIntegerField(default=0)
    reserved_fen = models.PositiveBigIntegerField(default=0)
    refunded_fen = models.PositiveBigIntegerField(default=0)
    class Meta:
        db_table = "aftersale_allocation"
        constraints = [models.CheckConstraint(condition=Q(purchased_qty__gt=0), name="aftersale_purchased_positive"),
            models.CheckConstraint(condition=Q(purchased_qty__gte=F("reserved_qty")+F("refunded_qty")), name="aftersale_qty_bounded"),
            models.CheckConstraint(condition=Q(payable_fen__gte=F("reserved_fen")+F("refunded_fen")), name="aftersale_money_bounded")]

class AfterSaleCase(models.Model):
    class Status(models.TextChoices):
        PENDING_REVIEW = "PENDING_REVIEW"
        WAITING_RETURN = "WAITING_RETURN"
        WAITING_REFUND = "WAITING_REFUND"
        COMPLETED = "COMPLETED"
        REJECTED = "REJECTED"
        WITHDRAWN = "WITHDRAWN"
    class Kind(models.TextChoices):
        REFUND_ONLY = "REFUND_ONLY"
        RETURN_REFUND = "RETURN_REFUND"
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_line = models.ForeignKey("orders.OrderLine", on_delete=models.PROTECT, related_name="aftersale_cases")
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    request_key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    amount_fen = models.PositiveBigIntegerField()
    used_quantity = models.PositiveIntegerField(default=0)
    unredeemed_quantity = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=13, choices=Kind.choices)
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING_REVIEW)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        db_table = "aftersale_case"
        indexes = [models.Index(fields=["status", "created_at"], name="aftersale_queue_idx")]
        constraints = [models.UniqueConstraint(fields=["member", "request_key"], name="aftersale_member_key"),
            models.UniqueConstraint(fields=["order_line"], condition=Q(status__in=["PENDING_REVIEW", "WAITING_RETURN", "WAITING_REFUND"]), name="aftersale_line_one_active"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="aftersale_case_qty_positive"),
            models.CheckConstraint(condition=Q(amount_fen__gte=0), name="aftersale_case_money_positive"),
            models.CheckConstraint(condition=Q(quantity__gte=F("used_quantity")+F("unredeemed_quantity")), name="aftersale_scope_bounded"),
            models.CheckConstraint(condition=Q(status__in=["PENDING_REVIEW", "WAITING_RETURN", "WAITING_REFUND", "COMPLETED", "REJECTED", "WITHDRAWN"]), name="aftersale_status_valid"),
            models.CheckConstraint(condition=Q(kind__in=["REFUND_ONLY", "RETURN_REFUND"]), name="aftersale_kind_valid")]

class AfterSaleEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(AfterSaleCase, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=16)
    status = models.CharField(max_length=16)
    actor = models.ForeignKey("accounts.AdminAccount", null=True, on_delete=models.PROTECT)
    reason = models.CharField(max_length=500)
    occurred_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "aftersale_event"


class ReturnShipment(models.Model):
    """Append-only member corrections; latest case revision is the current logistics."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(AfterSaleCase, on_delete=models.PROTECT, related_name="return_shipments")
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    request_key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    expected_revision = models.PositiveIntegerField()
    revision = models.PositiveIntegerField()
    carrier_name = models.CharField(max_length=80)
    tracking_no = models.CharField(max_length=80)
    submitted_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "aftersale_return_shipment"
        constraints = [models.UniqueConstraint(fields=["member","request_key"], name="return_shipment_member_key"),
                       models.UniqueConstraint(fields=["case","revision"], name="return_shipment_case_revision")]
        indexes = [models.Index(fields=["case","-revision"],name="return_shipment_latest_idx")]


class ReturnAcceptance(models.Model):
    """One final, immutable warehouse/refund decision per return application."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(AfterSaleCase, on_delete=models.PROTECT, related_name="return_acceptance")
    actor = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    request_key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    expected_revision = models.PositiveIntegerField()
    mode = models.CharField(max_length=13)
    received_quantity = models.PositiveIntegerField()
    salable_quantity = models.PositiveIntegerField()
    refund_quantity = models.PositiveIntegerField()
    refund_amount_fen = models.PositiveBigIntegerField()
    max_refund_amount_fen = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=500)
    accepted_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "aftersale_return_acceptance"
        constraints = [models.UniqueConstraint(fields=["actor","request_key"],name="return_acceptance_actor_key"),
            models.CheckConstraint(condition=Q(mode__in=["RECEIVED","WAIVED_RETURN"]), name="return_acceptance_mode"),
            models.CheckConstraint(condition=Q(salable_quantity__lte=F("received_quantity")), name="return_salable_bounded"),
            models.CheckConstraint(condition=Q(refund_amount_fen__lte=F("max_refund_amount_fen")), name="return_amount_bounded"),
            models.CheckConstraint(condition=Q(refund_quantity=0,refund_amount_fen=0)|Q(refund_quantity__gt=0,refund_amount_fen__gte=0),name="return_refund_shape"),
            models.CheckConstraint(condition=Q(mode="RECEIVED",refund_quantity__lte=F("received_quantity"))|Q(mode="WAIVED_RETURN",received_quantity=0,salable_quantity=0), name="return_receipt_shape")]


class RefundAmountSnapshot(models.Model):
    case = models.OneToOneField(AfterSaleCase, primary_key=True, on_delete=models.PROTECT, related_name="refund_amount_snapshot")
    goods_fen = models.PositiveBigIntegerField()
    shipping_fen = models.PositiveBigIntegerField(default=0)
    total_fen = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "aftersale_refund_amount_snapshot"
        constraints = [models.CheckConstraint(condition=Q(total_fen=F("goods_fen")+F("shipping_fen")),name="refund_amount_components")]


class OrderShippingRefundClaim(models.Model):
    order = models.OneToOneField("orders.Order", primary_key=True, on_delete=models.PROTECT)
    case = models.OneToOneField(AfterSaleCase, on_delete=models.PROTECT)
    amount_fen = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "aftersale_shipping_refund_claim"
        constraints = [models.CheckConstraint(condition=Q(amount_fen__gt=0), name="shipping_refund_positive")]


class BenefitOnlySettlement(models.Model):
    case = models.OneToOneField(AfterSaleCase, primary_key=True, on_delete=models.PROTECT)
    actor = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    request_key = models.UUIDField()
    expected_revision = models.PositiveIntegerField()
    settled_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "aftersale_benefit_settlement"
        constraints = [models.UniqueConstraint(fields=["actor","request_key"], name="benefit_settlement_actor_key")]


class StoreAfterSaleNote(models.Model):
    """Store staff advice/receipt facts; platform owns final stock and money decisions."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(AfterSaleCase, on_delete=models.PROTECT, related_name='store_notes')
    actor = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    kind = models.CharField(max_length=7, choices=[('ADVICE','Advice'),('RECEIPT','Receipt')])
    note = models.CharField(max_length=500)
    received_quantity = models.PositiveIntegerField(default=0)
    salable_quantity = models.PositiveIntegerField(default=0)
    request_key = models.UUIDField(unique=True)
    request_digest = models.CharField(max_length=64)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'aftersale_store_note'
        constraints = [models.CheckConstraint(condition=models.Q(salable_quantity__lte=models.F('received_quantity')),name='store_note_salable_bounded')]
