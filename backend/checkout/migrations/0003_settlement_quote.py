from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    dependencies = [("checkout", "0002_checkoutquote_source_digest_and_more"),
                    ("shipping", "0001_initial")]
    operations = [
        migrations.AddField(model_name="checkoutquote", name="shipping_fee_fen",
                            field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name="checkoutquote", name="shipping_policy_revision",
                            field=models.PositiveIntegerField(default=1)),
        migrations.AddField(model_name="checkoutquote", name="coupon_id",
                            field=models.UUIDField(null=True, blank=True)),
        migrations.AddField(model_name="checkoutquote", name="coupon_discount_fen",
                            field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name="checkoutquote", name="points_to_use",
                            field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="checkoutquote", name="points_discount_fen",
                            field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name="checkoutquote", name="allocations",
                            field=models.JSONField(default=list)),
        migrations.AddConstraint(model_name="checkoutquote", constraint=models.CheckConstraint(
            condition=Q(shipping_fee_fen__gte=0), name="quote_shipping_nonnegative")),
        migrations.AddConstraint(model_name="checkoutquote", constraint=models.CheckConstraint(
            condition=Q(coupon_discount_fen__gte=0), name="quote_coupon_nonnegative")),
        migrations.AddConstraint(model_name="checkoutquote", constraint=models.CheckConstraint(
            condition=Q(points_discount_fen__gte=0), name="quote_points_nonnegative")),
        migrations.AddConstraint(model_name="checkoutquote", constraint=models.CheckConstraint(
            condition=Q(coupon_discount_fen__lte=F("goods_total_fen")), name="quote_coupon_within_goods")),
        migrations.AddConstraint(model_name="checkoutquote", constraint=models.CheckConstraint(
            condition=Q(points_discount_fen__lte=F("goods_total_fen") - F("coupon_discount_fen")),
            name="quote_points_within_goods")),
        migrations.AddConstraint(model_name="checkoutquote", constraint=models.CheckConstraint(
            condition=Q(payable_fen=F("goods_total_fen") + F("shipping_fee_fen")
                                    - F("coupon_discount_fen") - F("points_discount_fen")),
            name="quote_total_matches_parts")),
    ]
