from django.db import migrations


def install(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':
        return
    schema_editor.execute("""
    CREATE FUNCTION dining_withdrawal_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Dining withdrawal evidence cannot be deleted'; END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.status <> 'PENDING' OR NEW.revision <> 1 THEN RAISE EXCEPTION 'Dining withdrawal starts pending'; END IF;
      ELSIF (NEW.id,NEW.restaurant_id,NEW.member_id,NEW.request_key,NEW.amount_fen,NEW.created_at)
          IS DISTINCT FROM (OLD.id,OLD.restaurant_id,OLD.member_id,OLD.request_key,OLD.amount_fen,OLD.created_at)
        OR OLD.status <> 'PENDING' OR NEW.status NOT IN ('APPROVED','REJECTED') OR NEW.revision <> OLD.revision+1
      THEN RAISE EXCEPTION 'Invalid dining withdrawal transition'; END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    CREATE TRIGGER dining_withdrawal_evidence BEFORE INSERT OR UPDATE OR DELETE
    ON dining_alliance_withdrawal FOR EACH ROW EXECUTE FUNCTION dining_withdrawal_guard();
    """)


def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':
        return
    schema_editor.execute('DROP TRIGGER IF EXISTS dining_withdrawal_evidence ON dining_alliance_withdrawal; DROP FUNCTION IF EXISTS dining_withdrawal_guard();')


class Migration(migrations.Migration):
    dependencies=[('dining_alliance','0003_diningorderlinesnapshot_dining_line_configured_valid_and_more')]
    operations=[migrations.RunPython(install,uninstall)]
