from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0007_product_redeem_valid_until")]
    operations = [
        migrations.AddIndex(model_name="asset", index=models.Index(
            fields=["-created_at", "-id"], name="asset_created_page_idx")),
        migrations.AddIndex(model_name="asset", index=models.Index(
            fields=["kind", "-created_at"], name="asset_kind_created_idx")),
        migrations.AddIndex(model_name="asset", index=models.Index(
            fields=["created_by", "-created_at"], name="asset_owner_created_idx")),
    ]
