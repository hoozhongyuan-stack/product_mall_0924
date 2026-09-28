"""Independent points prices, immutable ten-minute quotes and scoped receipts."""
import uuid
from django.db import models
from django.db.models import Q


class ExchangeOffer(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    sku=models.OneToOneField('catalog.Sku',on_delete=models.PROTECT)
    points_price=models.PositiveIntegerField()
    status=models.CharField(max_length=8,default='DRAFT')
    revision=models.PositiveIntegerField(default=1)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table='points_exchange_offer'
        indexes=[models.Index(fields=['status','-created_at'],name='exchange_offer_status_idx')]
        constraints=[models.CheckConstraint(condition=Q(points_price__gte=1,points_price__lte=1000000),name='exchange_price_bounded'),
                     models.CheckConstraint(condition=Q(status__in=['DRAFT','ON_SALE','OFF_SALE']),name='exchange_offer_state_valid')]


class ExchangeQuote(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    member=models.ForeignKey('customers.Member',on_delete=models.PROTECT)
    offer=models.ForeignKey(ExchangeOffer,on_delete=models.PROTECT)
    address_id=models.UUIDField(null=True)
    snapshot=models.JSONField()
    digest=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)
    expires_at=models.DateTimeField()

    class Meta:
        db_table='points_exchange_quote'
        indexes=[models.Index(fields=['member','-created_at'],name='exchange_quote_member_idx')]


class ExchangeOperation(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    actor=models.ForeignKey('accounts.AdminAccount',on_delete=models.PROTECT)
    key=models.UUIDField()
    action=models.CharField(max_length=12)
    offer=models.ForeignKey(ExchangeOffer,on_delete=models.PROTECT)
    digest=models.CharField(max_length=64)
    result=models.JSONField()
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table='points_exchange_operation'
        constraints=[models.UniqueConstraint(fields=['actor','key'],name='exchange_actor_key_unique')]
