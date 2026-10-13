"""Retain immutable historical facts while rejecting new misrouted express orders."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0018_fulfillment_governance_snapshot')]
    operations = [
        migrations.RemoveConstraint(model_name='order', name='order_store_mode_consistent'),
        migrations.AddConstraint(model_name='order', constraint=models.CheckConstraint(
            condition=(models.Q(store__isnull=True, delivery_mode__in=['', 'EXPRESS', 'REDEEM']) |
                       models.Q(store__isnull=False, delivery_mode__in=['PICKUP', 'DELIVERY', 'EXPRESS'])),
            name='order_store_mode_consistent')),
        migrations.RunSQL('''
            CREATE FUNCTION order_platform_express_insert_guard() RETURNS trigger AS $$
            BEGIN
                IF NEW.delivery_mode = 'EXPRESS' AND NEW.store_id IS NOT NULL THEN
                    RAISE EXCEPTION 'new express orders must use platform fulfillment';
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER order_platform_express_insert_guard BEFORE INSERT ON customer_order
            FOR EACH ROW EXECUTE FUNCTION order_platform_express_insert_guard();
        ''', '''DROP TRIGGER order_platform_express_insert_guard ON customer_order;
                DROP FUNCTION order_platform_express_insert_guard();'''),
    ]
