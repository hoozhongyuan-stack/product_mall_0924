from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('stores', '0003_store_finance'), ('catalog', '0008_asset_library_indexes')]
    operations = [migrations.AddField(model_name='store', name='logo', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, related_name='store_logos', to='catalog.asset'))]
