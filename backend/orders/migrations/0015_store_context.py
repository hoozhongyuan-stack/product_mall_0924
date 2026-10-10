from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('orders', '0014_order_order_paid_fact_idx'), ('stores', '0001_initial')]
    operations = [
        migrations.AddField(model_name='order', name='store', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to='stores.store')),
        migrations.AddField(model_name='order', name='delivery_mode', field=models.CharField(max_length=8, blank=True)),
        migrations.AddField(model_name='order', name='store_snapshot', field=models.JSONField(default=dict)),
    ]
