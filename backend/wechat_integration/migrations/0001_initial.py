import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_singleton(apps, schema_editor):
    apps.get_model('wechat_integration', 'MiniProgramIntegration').objects.using(
        schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name='MiniProgramIntegration',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('managed', models.BooleanField(default=False)),
                ('revision', models.PositiveIntegerField(default=0)),
                ('encrypted_payload', models.TextField(blank=True, default='')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_check_revision', models.PositiveIntegerField(null=True)),
                ('last_check_status', models.CharField(blank=True, default='', max_length=16)),
                ('last_check_code', models.CharField(blank=True, default='', max_length=32)),
                ('last_check_fingerprint', models.CharField(blank=True, default='', max_length=64)),
                ('last_check_at', models.DateTimeField(null=True)),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                to=settings.AUTH_USER_MODEL)),
            ],
            options={'constraints': [models.CheckConstraint(condition=models.Q(pk=1), name='wechat_integration_singleton')]},
        ),
        migrations.RunPython(seed_singleton, migrations.RunPython.noop),
    ]
