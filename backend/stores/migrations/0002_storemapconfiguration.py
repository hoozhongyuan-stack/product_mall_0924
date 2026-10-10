from django.db import migrations, models


def singleton(apps, schema_editor):
    apps.get_model('stores', 'StoreMapConfiguration').objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('stores','0001_initial')]
    operations = [
        migrations.CreateModel(name='StoreMapConfiguration', fields=[
            ('id',models.PositiveSmallIntegerField(default=1,editable=False,primary_key=True,serialize=False)),
            ('managed',models.BooleanField(default=False)),
            ('encrypted_payload',models.TextField(blank=True)),
            ('revision',models.PositiveIntegerField(default=0)),
            ('updated_at',models.DateTimeField(auto_now=True)),
        ],options={'constraints':[models.CheckConstraint(condition=models.Q(id=1),name='store_map_singleton')]}),
        migrations.RunPython(singleton,migrations.RunPython.noop),
    ]
