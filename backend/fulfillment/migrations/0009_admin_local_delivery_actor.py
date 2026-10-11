from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('fulfillment', '0008_shipment_staff_actor'), ('accounts', '0001_initial')]
    operations = [
        migrations.AlterField(model_name='storedeliveryevent', name='actor', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='customers.member')),
        migrations.AddField(model_name='storedeliveryevent', name='admin_actor', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='accounts.adminaccount')),
        migrations.AddConstraint(model_name='storedeliveryevent', constraint=models.CheckConstraint(condition=models.Q(models.Q(actor__isnull=False, admin_actor__isnull=True), models.Q(actor__isnull=True, admin_actor__isnull=False), _connector='OR'), name='store_delivery_event_actor_one')),
    ]
