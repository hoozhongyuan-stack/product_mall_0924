from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE FUNCTION inventory_ledger_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'confirmed inventory ledger is immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER inventory_ledger_immutable
          BEFORE UPDATE OR DELETE ON inventory_ledger
          FOR EACH ROW EXECUTE FUNCTION inventory_ledger_reject_mutation();
        """,
        reverse_sql="""
        DROP TRIGGER inventory_ledger_immutable ON inventory_ledger;
        DROP FUNCTION inventory_ledger_reject_mutation();
        """,
    )]
