from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0008_adminreadquota')]
    operations = [
        migrations.RemoveConstraint(model_name='adminreadquota', name='admin_read_quota_scope'),
        migrations.AddConstraint(model_name='adminreadquota', constraint=models.CheckConstraint(
            condition=models.Q(scope__in=['audit', 'business', 'wechat.integration']), name='admin_read_quota_scope')),
    ]
