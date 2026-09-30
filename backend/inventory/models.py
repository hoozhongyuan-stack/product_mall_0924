"""Inventory-owned records. Quantities are integer SKU base units."""

import uuid

from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Upper


class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=120)
    is_default = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "warehouse"
        constraints = [
            models.UniqueConstraint(Upper("code"), name="warehouse_code_upper_unique"),
            models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True),
                                    name="one_default_warehouse"),
        ]


class StockPool(models.Model):
    """One physical stock identity; anchor SKU preserves the existing balance key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    anchor_sku = models.OneToOneField("catalog.Sku", on_delete=models.PROTECT,
                                      related_name="anchored_stock_pool")
    base_unit = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_stock_pool"


class StockPoolSku(models.Model):
    """Explicit membership. Absence means the SKU owns an independent pool."""

    sku = models.OneToOneField("catalog.Sku", primary_key=True, on_delete=models.PROTECT,
                               related_name="stock_pool_membership")
    pool = models.ForeignKey(StockPool, on_delete=models.PROTECT, related_name="members")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_stock_pool_sku"


class InventoryBalance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="balances")
    sku = models.ForeignKey("catalog.Sku", on_delete=models.PROTECT, related_name="inventory_balances")
    on_hand_base_units = models.BigIntegerField(default=0)
    reserved_base_units = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_balance"
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "sku"], name="inventory_balance_wh_sku_unique"),
            models.CheckConstraint(condition=Q(on_hand_base_units__gte=0), name="inventory_on_hand_nonnegative"),
            models.CheckConstraint(condition=Q(reserved_base_units__gte=0), name="inventory_reserved_nonnegative"),
            models.CheckConstraint(condition=Q(reserved_base_units__lte=F("on_hand_base_units")),
                                   name="inventory_reserved_not_over_hand"),
        ]
        indexes = [models.Index(fields=["sku", "warehouse"], name="inventory_balance_sku_idx")]


class InventoryReservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RELEASED = "RELEASED", "Released"
        CONSUMED = "CONSUMED", "Consumed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_line = models.OneToOneField("orders.OrderLine", on_delete=models.PROTECT,
                                      related_name="reservation")
    balance = models.ForeignKey(InventoryBalance, on_delete=models.PROTECT)
    base_quantity = models.PositiveBigIntegerField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_reservation"
        constraints = [models.CheckConstraint(condition=Q(base_quantity__gt=0),
                                             name="reservation_quantity_positive")]


class InventoryReservationEvent(models.Model):
    class Action(models.TextChoices):
        RESERVE = "RESERVE", "Reserve"
        RELEASE = "RELEASE", "Release"
        CONSUME = "CONSUME", "Consume"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reservation = models.ForeignKey(InventoryReservation, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=8, choices=Action.choices)
    base_quantity = models.PositiveBigIntegerField()
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_reservation_event"
        constraints = [models.UniqueConstraint(fields=["reservation", "action"],
                                               name="reservation_action_unique"),
                       models.CheckConstraint(condition=Q(base_quantity__gt=0),
                                              name="reservation_event_quantity_positive")]


class InboundDocument(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        CONFIRMED = "CONFIRMED", "Confirmed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_no = models.CharField(max_length=40, unique=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.DRAFT)
    reason = models.CharField(max_length=200)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT,
                                   related_name="created_inbounds")
    creation_key = models.UUIDField(null=True, blank=True)
    creation_digest = models.CharField(max_length=64, blank=True)
    confirmed_by = models.ForeignKey("accounts.AdminAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="confirmed_inbounds")
    confirmation_key = models.UUIDField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_inbound"
        indexes = [models.Index(fields=["warehouse", "status", "-created_at"],
                                name="inbound_wh_status_created_idx")]
        constraints = [models.UniqueConstraint(fields=["created_by", "creation_key"],
                                               condition=Q(creation_key__isnull=False),
                                               name="inbound_actor_creation_key_unique")]


class InboundLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(InboundDocument, on_delete=models.PROTECT, related_name="lines")
    sku = models.ForeignKey("catalog.Sku", on_delete=models.PROTECT)
    unit_version = models.ForeignKey("catalog.SkuUnitVersion", on_delete=models.PROTECT)
    quantity = models.PositiveBigIntegerField()
    operation_unit = models.CharField(max_length=30)
    ratio = models.PositiveIntegerField()
    base_quantity = models.PositiveBigIntegerField()
    base_unit = models.CharField(max_length=30)

    class Meta:
        db_table = "inventory_inbound_line"
        constraints = [
            models.UniqueConstraint(fields=["document", "sku"], name="inbound_line_sku_unique"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="inbound_quantity_positive"),
            models.CheckConstraint(condition=Q(ratio__gt=0), name="inbound_ratio_positive"),
            models.CheckConstraint(condition=Q(base_quantity__gt=0), name="inbound_base_quantity_positive"),
        ]


class OutboundDocument(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        CONFIRMED = "CONFIRMED", "Confirmed"

    class Reason(models.TextChoices):
        DAMAGE = "DAMAGE", "Damage"
        SAMPLE = "SAMPLE", "Sample"
        INTERNAL = "INTERNAL", "Internal"
        OTHER = "OTHER", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_no = models.CharField(max_length=40, unique=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.DRAFT)
    reason = models.CharField(max_length=8, choices=Reason.choices)
    note = models.CharField(max_length=200, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT,
                                   related_name="created_outbounds")
    creation_key = models.UUIDField()
    creation_digest = models.CharField(max_length=64)
    confirmed_by = models.ForeignKey("accounts.AdminAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="confirmed_outbounds")
    confirmation_key = models.UUIDField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_outbound"
        indexes = [models.Index(fields=["warehouse", "status", "-created_at"],
                                name="outbound_wh_status_created_idx")]
        constraints = [models.UniqueConstraint(fields=["created_by", "creation_key"],
                                               name="outbound_actor_creation_key_unique")]


class OutboundLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(OutboundDocument, on_delete=models.PROTECT, related_name="lines")
    sku = models.ForeignKey("catalog.Sku", on_delete=models.PROTECT)
    unit_version = models.ForeignKey("catalog.SkuUnitVersion", on_delete=models.PROTECT)
    quantity = models.PositiveBigIntegerField()
    operation_unit = models.CharField(max_length=30)
    ratio = models.PositiveIntegerField()
    base_quantity = models.PositiveBigIntegerField()
    base_unit = models.CharField(max_length=30)

    class Meta:
        db_table = "inventory_outbound_line"
        constraints = [
            models.UniqueConstraint(fields=["document", "sku"], name="outbound_line_sku_unique"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="outbound_quantity_positive"),
            models.CheckConstraint(condition=Q(ratio__gt=0), name="outbound_ratio_positive"),
            models.CheckConstraint(condition=Q(base_quantity__gt=0), name="outbound_base_quantity_positive"),
        ]


class StocktakeDocument(models.Model):
    class Status(models.TextChoices):
        COUNTING = "COUNTING", "Counting"
        PENDING_REVIEW = "PENDING_REVIEW", "Pending review"
        APPROVED = "APPROVED", "Approved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_no = models.CharField(max_length=40, unique=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.COUNTING)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT,
                                   related_name="created_stocktakes")
    creation_key = models.UUIDField()
    creation_digest = models.CharField(max_length=64)
    submitted_by = models.ForeignKey("accounts.AdminAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="submitted_stocktakes")
    reviewed_by = models.ForeignKey("accounts.AdminAccount", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="reviewed_stocktakes")
    review_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_stocktake"
        indexes = [models.Index(fields=["warehouse", "status", "-created_at"],
                                name="stocktake_wh_status_idx")]
        constraints = [models.UniqueConstraint(fields=["created_by", "creation_key"],
                                               name="stocktake_actor_creation_key_unique")]


class StocktakeLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(StocktakeDocument, on_delete=models.PROTECT, related_name="lines")
    sku = models.ForeignKey("catalog.Sku", on_delete=models.PROTECT)
    unit_version = models.ForeignKey("catalog.SkuUnitVersion", on_delete=models.PROTECT)
    base_unit = models.CharField(max_length=30)
    book_at_start_base_units = models.BigIntegerField()
    book_at_submit_base_units = models.BigIntegerField(null=True, blank=True)
    reserved_at_submit_base_units = models.BigIntegerField(null=True, blank=True)
    balance_updated_at_at_submit = models.DateTimeField(null=True, blank=True)
    counted_base_units = models.BigIntegerField(null=True, blank=True)
    delta_base_units = models.BigIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "inventory_stocktake_line"
        constraints = [
            models.UniqueConstraint(fields=["document", "sku"], name="stocktake_line_sku_unique"),
            models.CheckConstraint(condition=Q(book_at_start_base_units__gte=0),
                                   name="stocktake_start_nonnegative"),
            models.CheckConstraint(condition=(Q(counted_base_units__isnull=True) |
                                              Q(counted_base_units__gte=0)),
                                   name="stocktake_count_nonnegative"),
        ]


class StocktakeAction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(StocktakeDocument, on_delete=models.PROTECT)
    actor = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    key = models.UUIDField(unique=True)
    action = models.CharField(max_length=16)
    request_digest = models.CharField(max_length=64)
    result_revision = models.PositiveIntegerField()
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_stocktake_action"


class InventoryLedger(models.Model):
    class MovementType(models.TextChoices):
        INBOUND = "INBOUND", "Inbound"
        OUTBOUND = "OUTBOUND", "Outbound"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        SALE = "SALE", "Sale"
        REFUND = "REFUND", "Unshipped refund"
        RETURN = "RETURN", "Accepted return"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    sku = models.ForeignKey("catalog.Sku", on_delete=models.PROTECT)
    unit_version = models.ForeignKey("catalog.SkuUnitVersion", on_delete=models.PROTECT)
    inbound_line = models.OneToOneField(InboundLine, null=True, blank=True, on_delete=models.PROTECT)
    outbound_line = models.OneToOneField(OutboundLine, null=True, blank=True, on_delete=models.PROTECT)
    stocktake_line = models.OneToOneField(StocktakeLine, null=True, blank=True, on_delete=models.PROTECT)
    order_line = models.OneToOneField("orders.OrderLine", null=True, blank=True, on_delete=models.PROTECT)
    refund_order_line = models.ForeignKey("orders.OrderLine", null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="refund_ledgers")
    refund_case_id = models.UUIDField(null=True, blank=True, unique=True)
    return_case_id = models.UUIDField(null=True, blank=True, unique=True)
    return_order_line = models.ForeignKey("orders.OrderLine", null=True, blank=True, on_delete=models.PROTECT, related_name="return_ledgers")
    movement_type = models.CharField(max_length=10, choices=MovementType.choices,
                                     default=MovementType.INBOUND)
    operation_unit = models.CharField(max_length=30)
    operation_quantity = models.PositiveBigIntegerField()
    ratio = models.PositiveIntegerField()
    delta_base_units = models.BigIntegerField()
    balance_before = models.BigIntegerField()
    balance_after = models.BigIntegerField()
    reason = models.CharField(max_length=200)
    note = models.CharField(max_length=200, blank=True)
    actor = models.ForeignKey("accounts.AdminAccount", null=True, blank=True, on_delete=models.PROTECT)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_ledger"
        indexes = [models.Index(fields=["warehouse", "sku", "-occurred_at"],
                                name="ledger_wh_sku_occurred_idx"),
                   models.Index(fields=["movement_type", "-occurred_at"],
                                name="ledger_type_occurred_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(operation_quantity__gt=0), name="ledger_operation_positive"),
            models.CheckConstraint(condition=(Q(movement_type="INBOUND", delta_base_units__gt=0,
                                                inbound_line__isnull=False, outbound_line__isnull=True,
                                                stocktake_line__isnull=True, order_line__isnull=True,
                                                actor__isnull=False) |
                                              Q(movement_type="OUTBOUND", delta_base_units__lt=0,
                                                inbound_line__isnull=True, outbound_line__isnull=False,
                                                stocktake_line__isnull=True, order_line__isnull=True,
                                                actor__isnull=False) |
                                              Q(movement_type="ADJUSTMENT", inbound_line__isnull=True,
                                                outbound_line__isnull=True, stocktake_line__isnull=False,
                                                order_line__isnull=True, actor__isnull=False) &
                                              (Q(delta_base_units__gt=0) | Q(delta_base_units__lt=0)) |
                                              Q(movement_type="SALE", delta_base_units__lt=0,
                                                inbound_line__isnull=True, outbound_line__isnull=True,
                                                stocktake_line__isnull=True, order_line__isnull=False,
                                                actor__isnull=True) |
                                              Q(movement_type="REFUND", delta_base_units__gt=0,
                                                inbound_line__isnull=True, outbound_line__isnull=True,
                                                stocktake_line__isnull=True, order_line__isnull=True,
                                                refund_order_line__isnull=False, refund_case_id__isnull=False) |
                                              Q(movement_type="RETURN", delta_base_units__gt=0, inbound_line__isnull=True, outbound_line__isnull=True,
                                                stocktake_line__isnull=True, order_line__isnull=True, return_case_id__isnull=False, return_order_line__isnull=False, actor__isnull=False)),
                                   name="ledger_direction_and_source_valid"),
            models.CheckConstraint(condition=Q(movement_type="REFUND", refund_order_line__isnull=False,
                                               refund_case_id__isnull=False) |
                                   (~Q(movement_type="REFUND") & Q(refund_order_line__isnull=True,
                                                                   refund_case_id__isnull=True)),
                                   name="ledger_refund_source_shape"),
            models.CheckConstraint(condition=Q(movement_type="RETURN",return_case_id__isnull=False,return_order_line__isnull=False)|
                                   (~Q(movement_type="RETURN") & Q(return_case_id__isnull=True,return_order_line__isnull=True)),name="ledger_return_source_shape"),
        ]


class ReturnDisposition(models.Model):
    """Received goods outside salable balance; damaged units are never deducted twice."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case_id = models.UUIDField(unique=True)
    acceptance_id = models.UUIDField(unique=True)
    order_line = models.ForeignKey("orders.OrderLine", on_delete=models.PROTECT)
    received_quantity = models.PositiveIntegerField()
    salable_quantity = models.PositiveIntegerField()
    damaged_quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=500)
    actor = models.ForeignKey("accounts.AdminAccount", on_delete=models.PROTECT)
    occurred_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        db_table = "inventory_return_disposition"
        constraints = [models.CheckConstraint(condition=Q(received_quantity=F("salable_quantity")+F("damaged_quantity")),name="return_disposition_quantities")]
