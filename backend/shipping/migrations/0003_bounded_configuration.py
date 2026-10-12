from django.db import migrations


class Migration(migrations.Migration):
    dependencies=[('shipping','0002_shippingpolicy_campaign_shippingpolicy_sku_weights_and_more')]
    operations=[migrations.RunSQL("""ALTER TABLE shipping_policy ADD CONSTRAINT shipping_config_bounded CHECK (
        jsonb_typeof(templates)='array' AND jsonb_array_length(templates)<=100
        AND jsonb_typeof(zones)='array' AND jsonb_array_length(zones)<=200
        AND jsonb_typeof(sku_weights)='array' AND jsonb_array_length(sku_weights)<=1000
        AND jsonb_typeof(campaign)='object');""",'ALTER TABLE shipping_policy DROP CONSTRAINT shipping_config_bounded;')]
