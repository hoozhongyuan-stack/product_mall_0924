from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('customers', '0009_address_location')]
    operations = [
        migrations.AddField(model_name='customeraddress', name='poi_name', field=models.CharField(max_length=120, blank=True, default='')),
        migrations.AddField(model_name='customeraddress', name='location_address', field=models.CharField(max_length=200, blank=True, default='')),
        migrations.AddField(model_name='customeraddress', name='door_detail', field=models.CharField(max_length=120, blank=True, default='')),
    ]
