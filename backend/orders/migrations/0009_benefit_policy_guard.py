from django.db import migrations


class Migration(migrations.Migration):
    dependencies=[('orders','0008_remove_order_order_points_ratio_v1_and_more'),
                  ('checkout','0004_checkoutquote_benefit_policy_snapshot'),
                  ('benefits','0011_pointspolicy_deduct_fen_pointspolicy_deduct_points_and_more')]
    operations=[migrations.RunSQL('''
        CREATE FUNCTION order_benefit_policy_immutable() RETURNS trigger AS $$
        BEGIN
            IF NEW.benefit_policy_snapshot IS DISTINCT FROM OLD.benefit_policy_snapshot
            THEN RAISE EXCEPTION 'order benefit policy snapshot is immutable'; END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER order_benefit_policy_immutable BEFORE UPDATE ON customer_order
            FOR EACH ROW EXECUTE FUNCTION order_benefit_policy_immutable();
        ALTER TABLE customer_order ADD CONSTRAINT order_policy_conversion_consistent CHECK (COALESCE(
          (benefit_policy_snapshot='{}'::jsonb AND points_discount_fen=points_to_use) OR
          (benefit_policy_snapshot ? 'deductPoints' AND benefit_policy_snapshot ? 'deductFen'
            AND (benefit_policy_snapshot->>'deductPoints')::bigint > 0
            AND (benefit_policy_snapshot->>'deductFen')::bigint > 0
            AND points_discount_fen=points_to_use::bigint * (benefit_policy_snapshot->>'deductFen')::bigint
                / (benefit_policy_snapshot->>'deductPoints')::bigint), FALSE));
    ''','''
        ALTER TABLE customer_order DROP CONSTRAINT order_policy_conversion_consistent;
        DROP TRIGGER order_benefit_policy_immutable ON customer_order;
        DROP FUNCTION order_benefit_policy_immutable();
    ''')]
