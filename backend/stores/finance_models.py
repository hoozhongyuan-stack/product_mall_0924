"""Store finance policy, immutable order facts and auditable offline withdrawals."""
import uuid
from django.db import models
from django.db.models import Q


class StoreSkuProfitRule(models.Model):
    sku = models.OneToOneField('catalog.Sku',primary_key=True,on_delete=models.PROTECT)
    unit_version = models.ForeignKey('catalog.SkuUnitVersion',null=True,on_delete=models.PROTECT)
    purchase_cost_fen = models.PositiveBigIntegerField()
    platform_share_bps = models.PositiveSmallIntegerField()
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(platform_share_bps__lte=10000),name='store_profit_ratio_bounded')]


