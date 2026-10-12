import uuid
from django.db import models
from django.db.models import Q
from business_numbers.service import procurement_number


class ProcurementSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    encrypted_instructions = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='procurement_settings_singleton')]


class ProcurementProof(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    store = models.ForeignKey('stores.Store', on_delete=models.PROTECT)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    stored_name = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=32)
    byte_size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class ProcurementOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    order_no = models.CharField(max_length=24, unique=True, default=procurement_number, editable=False)
    store = models.ForeignKey('stores.Store', on_delete=models.PROTECT)
    member = models.ForeignKey('customers.Member', on_delete=models.PROTECT)
    request_key = models.UUIDField(unique=True)
    request_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default='REVIEW')
    revision = models.PositiveIntegerField(default=1)
    goods_amount_fen = models.PositiveBigIntegerField()
    freight_fen = models.PositiveBigIntegerField(default=0)
    encrypted_instructions = models.TextField(blank=True)
    note = models.CharField(max_length=300, blank=True)
    carrier = models.CharField(max_length=80, blank=True)
    tracking = models.CharField(max_length=120, blank=True)
    proof = models.ForeignKey(ProcurementProof, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.CheckConstraint(condition=Q(status__in=['REVIEW','QUOTE','PAYMENT','PROOF','PAID','SHIPPED','EXCEPTION','RECEIVED','CANCEL_REVIEW','CANCELLED']), name='procurement_status_valid')]


class ProcurementLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    order = models.ForeignKey(ProcurementOrder, on_delete=models.PROTECT, related_name='lines')
    sku = models.ForeignKey('catalog.Sku', on_delete=models.PROTECT)
    unit_version = models.ForeignKey('catalog.SkuUnitVersion', on_delete=models.PROTECT)
    anchor_sku = models.ForeignKey('catalog.Sku', on_delete=models.PROTECT, related_name='procurement_anchors')
    product_name = models.CharField(max_length=120)
    spec_label = models.CharField(max_length=300)
    sale_unit = models.CharField(max_length=30)
    base_unit = models.CharField(max_length=30)
    ratio = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()
    purchase_cost_fen = models.PositiveBigIntegerField()
    unit_reviewed = models.BooleanField(default=False)
    reviewed_current_unit_id = models.UUIDField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order','sku'], name='procurement_line_unique'), models.CheckConstraint(condition=Q(quantity__gte=1,quantity__lte=999,ratio__gte=1), name='procurement_line_quantity_valid')]


class ProcurementEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    order = models.ForeignKey(ProcurementOrder, on_delete=models.PROTECT, related_name='events')
    request_key = models.UUIDField(unique=True)
    digest = models.CharField(max_length=64)
    kind = models.CharField(max_length=40)
    note = models.CharField(max_length=500, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    proof = models.ForeignKey(ProcurementProof, null=True, on_delete=models.PROTECT)
    admin = models.ForeignKey('accounts.AdminAccount', null=True, on_delete=models.PROTECT)
    member = models.ForeignKey('customers.Member', null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at','id']
        constraints = [models.CheckConstraint(condition=(Q(admin__isnull=False, member__isnull=True)|Q(admin__isnull=True,member__isnull=False)), name='procurement_event_actor_valid')]
