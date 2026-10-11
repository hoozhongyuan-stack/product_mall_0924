"""Dining access, listings and immutable order commission evidence."""
import uuid
from django.db import models
from django.db.models import Q


def table_code():
    return uuid.uuid4().hex


class Restaurant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creation_request_key = models.UUIDField(null=True, unique=True, editable=False)
    creation_digest = models.CharField(max_length=64, blank=True, editable=False)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=300)
    contact_name = models.CharField(max_length=60)
    contact_phone = models.CharField(max_length=30)
    latitude = models.DecimalField(max_digits=10, decimal_places=6)
    longitude = models.DecimalField(max_digits=10, decimal_places=6)
    assigned_store = models.ForeignKey('stores.Store', on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'id']
        constraints = [models.CheckConstraint(condition=Q(latitude__gte=-90, latitude__lte=90, longitude__gte=-180, longitude__lte=180), name='dining_coordinates_valid')]


class RestaurantStaff(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name='staff')
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    permissions = models.JSONField(default=list)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['restaurant', 'member'], name='dining_staff_unique')]


class DiningTable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True, default=table_code, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name='tables')
    number = models.CharField(max_length=40)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['number', 'id']
        constraints = [models.UniqueConstraint(fields=['restaurant', 'number'], name='dining_table_number_unique')]


class DiningSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT)
    member_auth_version = models.PositiveIntegerField()
    store = models.ForeignKey("stores.Store", on_delete=models.PROTECT)
    restaurant_revision = models.PositiveIntegerField()
    table_revision = models.PositiveIntegerField()
    store_revision = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['member', 'closed_at'], name='dining_member_session_idx')]


class RestaurantProduct(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT)
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    on_sale = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['restaurant', 'product'], name='dining_listing_unique')]


class ProductCommissionRule(models.Model):
    product = models.OneToOneField('catalog.Product', primary_key=True, on_delete=models.PROTECT)
    commission_bps = models.PositiveSmallIntegerField()
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(commission_bps__lte=10000), name='dining_ratio_bounded')]


class StoreSkuPurchaseCost(models.Model):
    store = models.ForeignKey('stores.Store', on_delete=models.PROTECT)
    sku = models.ForeignKey('catalog.Sku', on_delete=models.PROTECT)
    unit_version = models.ForeignKey('catalog.SkuUnitVersion', on_delete=models.PROTECT)
    purchase_cost_fen = models.PositiveBigIntegerField()
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store', 'sku'], name='dining_store_sku_cost_unique')]


class DiningOrderSnapshot(models.Model):
    order = models.OneToOneField('orders.Order', primary_key=True, on_delete=models.PROTECT, related_name='dining_snapshot')
    session = models.ForeignKey(DiningSession, on_delete=models.PROTECT)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT)
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT)
    store = models.ForeignKey('stores.Store', on_delete=models.PROTECT)
    restaurant_name = models.CharField(max_length=120)
    address = models.CharField(max_length=300)
    table_number = models.CharField(max_length=40)
    commission_fen = models.PositiveBigIntegerField(default=0)
    funding_source = models.CharField(max_length=32, blank=True, default='')
    configured = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DiningOrderLineSnapshot(models.Model):
    line = models.OneToOneField('orders.OrderLine', primary_key=True, on_delete=models.PROTECT)
    snapshot = models.ForeignKey(DiningOrderSnapshot, on_delete=models.PROTECT, related_name='lines')
    quantity = models.PositiveIntegerField()
    paid_fen = models.PositiveBigIntegerField()
    purchase_cost_fen = models.PositiveBigIntegerField(null=True)
    cost_source = models.CharField(max_length=20)
    cost_revision = models.PositiveIntegerField(null=True)
    commission_bps = models.PositiveSmallIntegerField(null=True)
    commission_revision = models.PositiveIntegerField(null=True)
    commission_fen = models.PositiveBigIntegerField(default=0)
    configured = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(commission_bps__isnull=True) | Q(commission_bps__lte=10000), name='dining_line_ratio_bounded'), models.CheckConstraint(condition=Q(configured=False) | Q(configured=True, purchase_cost_fen__isnull=False, cost_revision__isnull=False, commission_bps__isnull=False, commission_revision__isnull=False), name='dining_line_configured_valid'), models.CheckConstraint(condition=Q(quantity__gt=0),name='dining_line_quantity_positive')]


class Withdrawal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    request_key = models.UUIDField()
    amount_fen = models.PositiveBigIntegerField()
    status = models.CharField(max_length=12, default='PENDING')
    reason = models.CharField(max_length=500, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['restaurant', 'request_key'], name='dining_withdrawal_key_unique'), models.CheckConstraint(condition=Q(amount_fen__gt=0), name='dining_withdrawal_positive'), models.CheckConstraint(condition=Q(status__in=['PENDING', 'APPROVED', 'REJECTED']), name='dining_withdrawal_status_valid')]
