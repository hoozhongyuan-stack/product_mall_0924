import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_singleton(apps, schema_editor):
    apps.get_model('code_versions', 'CodeUploadKey').objects.using(
        schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [
        ('code_versions', '0003_codebuildjob_source_revision_codesourceprovenance'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='CodeUploadKey',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('app_id', models.CharField(blank=True, max_length=18)),
                ('encrypted_payload', models.TextField(blank=True)),
                ('revision', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'mini_code_upload_key',
                     'constraints': [models.CheckConstraint(condition=models.Q(pk=1),
                                                            name='mini_code_upload_key_singleton')]},
        ),
        migrations.RunPython(seed_singleton, migrations.RunPython.noop),
    ]
