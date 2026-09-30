from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('code_versions', '0007_releaseuploadjob_diagnostics')]

    operations = [
        migrations.AddField(
            model_name='releaseuploadjob', name='inner_platform_error_code',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='releaseuploadjob', name='platform_reason',
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
