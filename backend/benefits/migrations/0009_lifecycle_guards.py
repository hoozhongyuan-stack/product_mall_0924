from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('benefits', '0008_membercoupon_restored_valid_until'),
                    ('customers', '0002_gradethreshold_memberconsumption_consumptionorder_and_more')]
    operations = [migrations.RunSQL('''
        CREATE FUNCTION benefit_lifecycle_reject_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'benefit lifecycle fact is immutable'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER benefit_snapshot_immutable BEFORE UPDATE OR DELETE ON benefit_order_snapshot
            FOR EACH ROW EXECUTE FUNCTION benefit_lifecycle_reject_mutation();
        CREATE TRIGGER benefit_ledger_immutable BEFORE UPDATE OR DELETE ON benefit_lifecycle_ledger
            FOR EACH ROW EXECUTE FUNCTION benefit_lifecycle_reject_mutation();
        CREATE TRIGGER consumption_event_immutable BEFORE UPDATE OR DELETE ON customer_consumption_event
            FOR EACH ROW EXECUTE FUNCTION benefit_lifecycle_reject_mutation();
        CREATE FUNCTION benefit_lifecycle_identity_guard() RETURNS trigger AS $$
        BEGIN
            IF NEW.grant_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM benefit_points_grant WHERE id=NEW.grant_id AND member_id=NEW.member_id
            ) THEN RAISE EXCEPTION 'benefit ledger grant member mismatch'; END IF;
            IF NEW.order_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM benefit_order_snapshot WHERE order_id=NEW.order_id AND member_id=NEW.member_id
            ) THEN RAISE EXCEPTION 'benefit ledger order member mismatch'; END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER benefit_ledger_identity BEFORE INSERT ON benefit_lifecycle_ledger
            FOR EACH ROW EXECUTE FUNCTION benefit_lifecycle_identity_guard();
    ''', '''
        DROP TRIGGER benefit_ledger_identity ON benefit_lifecycle_ledger;
        DROP FUNCTION benefit_lifecycle_identity_guard();
        DROP TRIGGER consumption_event_immutable ON customer_consumption_event;
        DROP TRIGGER benefit_ledger_immutable ON benefit_lifecycle_ledger;
        DROP TRIGGER benefit_snapshot_immutable ON benefit_order_snapshot;
        DROP FUNCTION benefit_lifecycle_reject_mutation();
    ''')]
