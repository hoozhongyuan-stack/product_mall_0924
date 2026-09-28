from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("notifications", "0002_subscriptiontemplatedraft")]

    operations = [
        migrations.AddIndex(
            model_name="messagetask",
            index=models.Index(fields=["-created_at", "-id"], name="subscription_task_admin_idx"),
        ),
    ]
