from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('code_versions', '0006_releasereviewjob')]

    operations = [
        migrations.AddField(
            model_name='releaseuploadjob', name='failure_stage',
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name='releaseuploadjob', name='sdk_code',
            field=models.CharField(blank=True, max_length=48),
        ),
        migrations.AddField(
            model_name='releaseuploadjob', name='platform_error_code',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
