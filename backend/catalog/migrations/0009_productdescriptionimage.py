from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("catalog", "0008_asset_library_indexes")]
    operations = [migrations.CreateModel(
        name="ProductDescriptionImage",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                related_name="description_images", to="catalog.product")),
            ("asset", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="catalog.asset")),
        ],
        options={"db_table": "product_description_image", "constraints": [models.UniqueConstraint(
            fields=("product", "asset"), name="unique_product_description_asset")]},
    )]
