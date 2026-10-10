from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0015_store_context')]
    operations = [
        migrations.AddConstraint(model_name='order', constraint=models.CheckConstraint(
            condition=(models.Q(store__isnull=True, delivery_mode='', store_snapshot={}) |
                       models.Q(store__isnull=False, delivery_mode__in=['PICKUP', 'DELIVERY', 'EXPRESS'])),
            name='order_store_mode_consistent')),
        migrations.RunSQL('''
            CREATE FUNCTION order_store_context_immutable() RETURNS trigger AS $$
            BEGIN
                IF ROW(NEW.store_id, NEW.delivery_mode, NEW.store_snapshot) IS DISTINCT FROM
                   ROW(OLD.store_id, OLD.delivery_mode, OLD.store_snapshot)
                THEN RAISE EXCEPTION 'order store context is immutable'; END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql;
            CREATE TRIGGER order_store_context_immutable BEFORE UPDATE ON customer_order
            FOR EACH ROW EXECUTE FUNCTION order_store_context_immutable();
        ''', '''DROP TRIGGER order_store_context_immutable ON customer_order;
                DROP FUNCTION order_store_context_immutable();'''),
    ]
