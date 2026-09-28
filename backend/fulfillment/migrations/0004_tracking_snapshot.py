import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fulfillment", "0003_evidence_guards")]

    operations = [migrations.CreateModel(
        name="ShipmentTrackingSnapshot",
        fields=[
            ("shipment", models.OneToOneField(primary_key=True, serialize=False,
                on_delete=django.db.models.deletion.CASCADE, related_name="tracking_snapshot",
                to="fulfillment.shipment")),
            ("carrier_code", models.CharField(max_length=24)),
            ("tracking_no", models.CharField(max_length=80)),
            ("status", models.CharField(max_length=20, default="UNAVAILABLE")),
            ("events", models.JSONField(default=list)),
            ("checked_at", models.DateTimeField(blank=True, null=True)),
            ("next_check_at", models.DateTimeField()),
            ("lease_until", models.DateTimeField(blank=True, null=True)),
            ("lease_id", models.UUIDField(blank=True, null=True)),
        ],
        options={"db_table": "fulfillment_tracking_snapshot"},
    )]
