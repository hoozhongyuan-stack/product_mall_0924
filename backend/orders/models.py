"""Order snapshots and successful request keys. Inventory owns reservation rows."""

import uuid

from django.db import models
from django.db.models import Q


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "PENDING_PAYMENT", "Pending payment"
        PAID = "PAID", "Paid"
        CLOSED = "CLOSED", "Closed"

    class PaymentMethod(models.TextChoices):
        WECHAT = "WECHAT", "WeChat"
        OFFLINE = "OFFLINE", "Offline"
        POINTS = "POINTS", "Points"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_no = models.CharField(max_length=40, unique=True)
    order_kind = models.CharField(max_length=6,default="CASH")
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    quote_id = models.UUIDField()
    revision = models.PositiveIntegerField(default=1)
    payment_instructions = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING_PAYMENT)
    payment_method = models.CharField(max_length=8, choices=PaymentMethod.choices)
    address_snapshot = models.JSONField(default=dict)
    goods_total_fen = models.BigIntegerField()
    shipping_fee_fen = models.BigIntegerField()
    shipping_policy_revision = models.PositiveIntegerField(default=1)
    coupon_id = models.UUIDField(null=True, blank=True)
    coupon_discount_fen = models.BigIntegerField(default=0)
    benefit_policy_snapshot = models.JSONField(default=dict)
    points_to_use = models.PositiveIntegerField(default=0)
    points_discount_fen = models.BigIntegerField(default=0)
    payable_fen = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=20, blank=True)

    class Meta:
        db_table = "customer_order"
        indexes = [models.Index(fields=["-created_at", "-id"], name="order_admin_created_idx"),
                   models.Index(fields=["payment_method", "status", "-created_at"], name="order_method_status_idx"),
                   models.Index(fields=["member", "-created_at"], name="order_member_created_idx"),
                   models.Index(fields=["status", "expires_at"], name="order_status_expiry_idx")]
        constraints = [
            models.CheckConstraint(condition=(Q(order_kind="CASH",payment_method__in=["OFFLINE","WECHAT"]) |
                Q(order_kind="POINTS",payment_method="POINTS",goods_total_fen=0,shipping_fee_fen=0,coupon_id__isnull=True,
                  coupon_discount_fen=0,points_discount_fen=0,payable_fen=0,points_to_use__gte=1,points_to_use__lte=99000000)),name="order_kind_payment_valid"),
            models.CheckConstraint(
                condition=(Q(status="PENDING_PAYMENT", paid_at__isnull=True, closed_at__isnull=True) |
                           Q(status="PAID", paid_at__isnull=False, closed_at__isnull=True) |
                           Q(status="CLOSED", paid_at__isnull=True, closed_at__isnull=False)),
                name="order_payment_state_time_consistent"),
            models.CheckConstraint(condition=Q(goods_total_fen__gte=0), name="order_goods_nonnegative"),
            models.CheckConstraint(condition=Q(shipping_fee_fen__gte=0), name="order_shipping_nonnegative"),
            models.CheckConstraint(condition=Q(coupon_discount_fen__gte=0), name="order_coupon_nonnegative"),
            models.CheckConstraint(condition=Q(points_discount_fen__gte=0), name="order_points_nonnegative"),
            models.CheckConstraint(condition=Q(coupon_discount_fen__lte=models.F("goods_total_fen")),
                                   name="order_coupon_within_goods"),
            models.CheckConstraint(condition=Q(points_discount_fen__lte=models.F("goods_total_fen") - models.F("coupon_discount_fen")),
                                   name="order_points_within_goods"),

            models.CheckConstraint(condition=Q(payable_fen__gte=0), name="order_payable_nonnegative"),
            models.CheckConstraint(condition=Q(payable_fen=models.F("goods_total_fen") + models.F("shipping_fee_fen")
                                             - models.F("coupon_discount_fen") - models.F("points_discount_fen")),
                                   name="order_total_matches_parts"),
        ]


class OrderLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="lines")
    product_id = models.UUIDField()
    sku = models.ForeignKey("catalog.Sku", on_delete=models.PROTECT)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT)
    unit_version = models.ForeignKey("catalog.SkuUnitVersion", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=120)
    sku_code = models.CharField(max_length=64)
    specs_snapshot = models.JSONField(default=list)
    fulfillment_kind = models.CharField(max_length=6)
    redeem_valid_until = models.DateField(null=True, blank=True)
    base_unit = models.CharField(max_length=30)
    sale_unit = models.CharField(max_length=30)
    ratio = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()
    base_quantity = models.PositiveBigIntegerField()
    points_unit_price = models.PositiveIntegerField(default=0)
    points_total = models.PositiveIntegerField(default=0)
    unit_price_fen = models.BigIntegerField()
    goods_amount_fen = models.BigIntegerField()
    coupon_discount_fen = models.BigIntegerField(default=0)
    points_discount_fen = models.BigIntegerField(default=0)
    payable_fen = models.BigIntegerField()

    class Meta:
        db_table = "customer_order_line"
        constraints = [
            models.UniqueConstraint(fields=["order", "sku"], name="order_line_sku_unique"),
            models.CheckConstraint(condition=(Q(points_unit_price=0,points_total=0) |
                Q(points_unit_price__gte=1,points_unit_price__lte=1000000,quantity__lte=99,
                  points_total=models.F("points_unit_price")*models.F("quantity"),unit_price_fen=0,goods_amount_fen=0,
                  coupon_discount_fen=0,points_discount_fen=0,payable_fen=0)),name="order_line_points_valid"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="order_line_quantity_positive"),
            models.CheckConstraint(condition=Q(base_quantity__gt=0), name="order_line_base_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price_fen__gte=0), name="order_line_price_nonnegative"),
            models.CheckConstraint(condition=Q(goods_amount_fen__gte=0), name="order_line_goods_nonnegative"),
            models.CheckConstraint(condition=Q(coupon_discount_fen__gte=0), name="order_line_coupon_nonnegative"),
            models.CheckConstraint(condition=Q(points_discount_fen__gte=0), name="order_line_points_nonnegative"),
            models.CheckConstraint(condition=Q(coupon_discount_fen__lte=models.F("goods_amount_fen")),
                                   name="order_line_coupon_within_goods"),
            models.CheckConstraint(condition=Q(points_discount_fen__lte=models.F("goods_amount_fen") - models.F("coupon_discount_fen")),
                                   name="order_line_points_within_goods"),
            models.CheckConstraint(condition=Q(payable_fen__gte=0), name="order_line_payable_nonnegative"),
            models.CheckConstraint(condition=Q(payable_fen=models.F("goods_amount_fen")
                                             - models.F("coupon_discount_fen") - models.F("points_discount_fen")),
                                   name="order_line_total_matches_parts"),
        ]


class OrderIdempotency(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey("customers.Member", on_delete=models.PROTECT)
    scope = models.CharField(max_length=32, default="CREATE_ORDER")
    key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    order = models.ForeignKey(Order, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_idempotency"
        constraints = [models.UniqueConstraint(fields=["member", "scope", "key"],
                                               name="order_actor_scope_key_unique")]
