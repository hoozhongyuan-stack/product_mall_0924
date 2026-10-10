from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('customers', '0008_member_profile_quota')]
    operations = [
        migrations.AddField(model_name='customeraddress', name='latitude', field=models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)),
        migrations.AddField(model_name='customeraddress', name='longitude', field=models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)),
    ]
