from django.db import migrations, models
from django.db.models import Q


def create_default_policy(apps, schema_editor):
    Policy = apps.get_model("shipping", "ShippingPolicy")
    Policy.objects.using(schema_editor.connection.alias).create(
        id=1, fee_fen=1000, delivery_scope="NATIONWIDE", revision=1)


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="ShippingPolicy",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("fee_fen", models.PositiveIntegerField(default=1000)),
                ("delivery_scope", models.CharField(default="NATIONWIDE", max_length=16)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "shipping_policy", "constraints": [
                models.CheckConstraint(condition=Q(id=1), name="shipping_policy_singleton"),
                models.CheckConstraint(condition=Q(fee_fen__lte=1_000_000), name="shipping_fee_bounded"),
                models.CheckConstraint(condition=Q(revision__gte=1), name="shipping_revision_positive"),
                models.CheckConstraint(condition=Q(delivery_scope="NATIONWIDE"), name="shipping_scope_nationwide"),
            ]},
        ),
        migrations.RunPython(create_default_policy, migrations.RunPython.noop),
    ]
