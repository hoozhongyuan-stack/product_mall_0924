"""Platform-managed stores; stocks remain in the inventory domain."""
import uuid
from django.db import models


class Store(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    warehouse = models.OneToOneField('inventory.Warehouse', on_delete=models.PROTECT, related_name='store')
    name = models.CharField(max_length=120)
    contact_name = models.CharField(max_length=60)
    contact_phone = models.CharField(max_length=30)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=80, blank=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    opening_hours = models.CharField(max_length=120, default='09:00–21:00')
    enabled = models.BooleanField(default=True)
    accepting_orders = models.BooleanField(default=True)
    supported_modes = models.JSONField(default=list)
    delivery_radius_meters = models.PositiveIntegerField(null=True, blank=True)
    delivery_fee_fen = models.PositiveIntegerField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        constraints = [models.CheckConstraint(condition=(models.Q(latitude__isnull=True,longitude__isnull=True) | models.Q(latitude__gte=-90,latitude__lte=90,longitude__gte=-180,longitude__lte=180)),name='store_coordinate_pair_valid')]


class StoreStaff(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='staff')
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT, related_name='store_memberships')
    permissions = models.JSONField(default=list)
    enabled = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store','member'],name='store_staff_unique')]


class StoreProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='products')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='store_listings')
    on_sale = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store','product'],name='store_product_unique')]


class StoreRequestQuota(models.Model):
    source_digest = models.CharField(max_length=64)
    scope = models.CharField(max_length=80)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source_digest','scope'],name='store_request_quota_unique')]


class StoreMapConfiguration(models.Model):
    """Singleton encrypted AMap credentials; no plaintext credential columns."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    managed = models.BooleanField(default=False)
    encrypted_payload = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='store_map_singleton')]

from .finance_models import StoreSkuProfitRule
