"""Make a product's sale state follow its SKU states."""

import uuid

from django.db import migrations, models
from django.db.models import Exists, F, OuterRef


def reconcile_sale_state(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Sku = apps.get_model("catalog", "Sku")
    AuditLog = apps.get_model("accounts", "AuditLog")
    live_sku = Sku.objects.filter(product_id=OuterRef("pk"), sale_status="ON_SALE")
    products = Product.objects.select_related("category__parent").annotate(has_live_sku=Exists(live_sku))
    for product in products.iterator():
        if product.has_live_sku and (not product.main_image_id or
                                     product.category.status != "ACTIVE" or
                                     not product.category.parent_id or
                                     product.category.parent.status != "ACTIVE"):
            raise RuntimeError(f"Product {product.id} has an on-sale SKU but is not ready for sale")
        ever_on_sale = product.has_live_sku or product.status != "DRAFT"
        status = "ON_SALE" if product.has_live_sku else "OFF_SALE" if ever_on_sale else "DRAFT"
        if product.status == status and product.ever_on_sale == ever_on_sale:
            continue
        before = {"status": product.status, "everOnSale": product.ever_on_sale}
        Product.objects.filter(pk=product.pk).update(status=status, ever_on_sale=ever_on_sale,
                                                     revision=F("revision") + 1)
        AuditLog.objects.create(actor_id=None, action_code="product.sale_state.reconciled",
                                object_type="product", object_id=str(product.id), before=before,
                                after={"status": status, "everOnSale": ever_on_sale},
                                result="SUCCESS", request_id=uuid.uuid4())


CHECK_FUNCTION = """
CREATE FUNCTION catalog_check_product_sale_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_id uuid;
    saved_status text;
    ever_sold boolean;
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
    SELECT status, ever_on_sale INTO saved_status, ever_sold FROM product WHERE id = target_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT EXISTS(SELECT 1 FROM sku WHERE product_id = target_id AND sale_status = 'ON_SALE')
      INTO has_live_sku;
    expected_status := CASE WHEN has_live_sku THEN 'ON_SALE'
                            WHEN ever_sold THEN 'OFF_SALE' ELSE 'DRAFT' END;
    IF saved_status <> expected_status OR (has_live_sku AND NOT ever_sold) THEN
        RAISE EXCEPTION 'Product sale state must follow SKU sale states'
          USING ERRCODE = '23514', CONSTRAINT = 'product_sku_sale_consistent';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER product_sku_sale_guard
  AFTER INSERT OR UPDATE OR DELETE ON product
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION catalog_check_product_sale_state();
CREATE CONSTRAINT TRIGGER sku_product_sale_guard
  AFTER INSERT OR UPDATE OR DELETE ON sku
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION catalog_check_product_sale_state();
"""


DROP_CHECK = """
DROP TRIGGER IF EXISTS sku_product_sale_guard ON sku;
DROP TRIGGER IF EXISTS product_sku_sale_guard ON product;
DROP FUNCTION IF EXISTS catalog_check_product_sale_state();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0005_alter_asset_kind'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='ever_on_sale',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(reconcile_sale_state, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(
                condition=(models.Q(status='DRAFT', ever_on_sale=False) |
                           models.Q(status__in=['ON_SALE', 'OFF_SALE'], ever_on_sale=True)),
                name='product_sale_lifecycle_consistent',
            ),
        ),
        migrations.RunSQL(CHECK_FUNCTION, DROP_CHECK),
    ]
