import uuid

import django.db.models.deletion
from django.db import migrations, models


def backfill_pools(apps, schema_editor):
    Sku = apps.get_model("catalog", "Sku")
    StockPool = apps.get_model("inventory", "StockPool")
    StockPoolSku = apps.get_model("inventory", "StockPoolSku")
    for sku in Sku.objects.select_related("current_unit").iterator():
        if sku.current_unit_id is None:
            continue
        pool = StockPool.objects.create(anchor_sku_id=sku.id, base_unit=sku.current_unit.base_unit)
        StockPoolSku.objects.create(sku_id=sku.id, pool_id=pool.id)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0010_returndisposition_and_more"),
        ("catalog", "0009_productdescriptionimage"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockPool",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("base_unit", models.CharField(max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("anchor_sku", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                     related_name="anchored_stock_pool", to="catalog.sku")),
            ],
            options={"db_table": "inventory_stock_pool"},
        ),
        migrations.CreateModel(
            name="StockPoolSku",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("pool", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                            related_name="members", to="inventory.stockpool")),
                ("sku", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, primary_key=True,
                                              related_name="stock_pool_membership", serialize=False,
                                              to="catalog.sku")),
            ],
            options={"db_table": "inventory_stock_pool_sku"},
        ),
        migrations.RunPython(backfill_pools, migrations.RunPython.noop),
    ]
