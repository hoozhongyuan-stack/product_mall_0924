from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_config(apps, schema_editor):
    apps.get_model('wechat_open_platform', 'ComponentConfig').objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name='ComponentConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('component_app_id', models.CharField(blank=True, max_length=64)),
                ('developer_app_id', models.CharField(blank=True, max_length=64)),
                ('redirect_uri', models.URLField(blank=True, max_length=512)),
                ('encrypted_credentials', models.TextField(blank=True)),
                ('encrypted_ticket', models.TextField(blank=True)),
                ('ticket_received_at', models.DateTimeField(blank=True, null=True)),
                ('ticket_event_time', models.PositiveBigIntegerField(default=0)),
                ('encrypted_access_token', models.TextField(blank=True)),
                ('access_token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('revision', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='AuthorizerGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('component_app_id', models.CharField(max_length=64)),
                ('authorizer_app_id', models.CharField(max_length=64, unique=True)),
                ('encrypted_refresh_token', models.TextField(blank=True)),
                ('encrypted_access_token', models.TextField(blank=True)),
                ('access_token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('scope_ids', models.JSONField(default=list)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('account_status', models.IntegerField(blank=True, null=True)),
                ('last_event_time', models.PositiveBigIntegerField(default=0)),
                ('revision', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='AuthorizationIntent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('state_hash', models.CharField(max_length=64, unique=True)),
                ('target_app_id', models.CharField(max_length=64)),
                ('component_revision', models.PositiveIntegerField()),
                ('expires_at', models.DateTimeField()),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                            to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(seed_config, migrations.RunPython.noop),
    ]
