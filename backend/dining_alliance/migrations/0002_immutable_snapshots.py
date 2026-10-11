from django.db import migrations


def install(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':
        return
    schema_editor.execute("""
    CREATE FUNCTION dining_reject_snapshot_change() RETURNS trigger AS $$
    BEGIN RAISE EXCEPTION 'Dining commission snapshots are immutable'; END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER dining_order_snapshot_immutable BEFORE UPDATE OR DELETE
    ON dining_alliance_diningordersnapshot FOR EACH ROW EXECUTE FUNCTION dining_reject_snapshot_change();
    CREATE TRIGGER dining_line_snapshot_immutable BEFORE UPDATE OR DELETE
    ON dining_alliance_diningorderlinesnapshot FOR EACH ROW EXECUTE FUNCTION dining_reject_snapshot_change();
    """)


def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':
        return
    schema_editor.execute("""
    DROP TRIGGER IF EXISTS dining_order_snapshot_immutable ON dining_alliance_diningordersnapshot;
    DROP TRIGGER IF EXISTS dining_line_snapshot_immutable ON dining_alliance_diningorderlinesnapshot;
    DROP FUNCTION IF EXISTS dining_reject_snapshot_change();
    """)


class Migration(migrations.Migration):
    dependencies=[('dining_alliance','0001_initial')]
    operations=[migrations.RunPython(install,uninstall)]
