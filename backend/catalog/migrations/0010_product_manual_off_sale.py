from django.db import migrations, models


CHECK_FUNCTION = """
CREATE OR REPLACE FUNCTION catalog_check_product_sale_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_id uuid;
    saved_status text;
    ever_sold boolean;
    manual_off boolean;
    has_live_sku boolean;
    expected_status text;
BEGIN
    IF TG_TABLE_NAME = 'product' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSIF TG_OP = 'DELETE' THEN
        target_id := OLD.product_id;
    ELSE
        target_id := NEW.product_id;
    END IF;
    SELECT status, ever_on_sale, manually_off_sale
      INTO saved_status, ever_sold, manual_off FROM product WHERE id = target_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT EXISTS(SELECT 1 FROM sku WHERE product_id = target_id AND sale_status = 'ON_SALE')
      INTO has_live_sku;
    expected_status := CASE WHEN manual_off THEN 'OFF_SALE'
                            WHEN has_live_sku THEN 'ON_SALE'
                            WHEN ever_sold THEN 'OFF_SALE' ELSE 'DRAFT' END;
    IF saved_status <> expected_status OR (has_live_sku AND NOT ever_sold)
       OR (manual_off AND NOT ever_sold) THEN
        RAISE EXCEPTION 'Product sale state conflicts with SKU state or manual gate'
          USING ERRCODE = '23514', CONSTRAINT = 'product_sku_sale_consistent';
    END IF;
    RETURN NULL;
END;
$$;
"""


def install_guard(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CHECK_FUNCTION)


def restore_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    # The old guard cannot represent a manually hidden SPU with live SKUs.
    if apps.get_model("catalog", "Product").objects.filter(manually_off_sale=True).exists():
        raise RuntimeError("Clear manually hidden products before reversing this migration")
    from importlib import import_module
    original = import_module("catalog.migrations.0006_sku_driven_product_sale").CHECK_FUNCTION
    schema_editor.execute(original.split("CREATE CONSTRAINT TRIGGER")[0].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


class Migration(migrations.Migration):
    dependencies = [("catalog", "0009_productdescriptionimage")]

    operations = [
        migrations.AddField(
            model_name="product", name="manually_off_sale", field=models.BooleanField(default=False),
        ),
        migrations.RunPython(install_guard, restore_guard),
    ]
