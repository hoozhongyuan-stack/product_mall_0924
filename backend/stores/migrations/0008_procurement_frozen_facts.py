from django.db import migrations

FORWARD="""
CREATE FUNCTION procurement_immutable_event() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'procurement event is immutable'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER procurement_event_immutable BEFORE UPDATE OR DELETE ON stores_procurementevent FOR EACH ROW EXECUTE FUNCTION procurement_immutable_event();
CREATE FUNCTION procurement_frozen_line() RETURNS trigger AS $$
BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(NEW)-'unit_reviewed'-'reviewed_current_unit_id') IS DISTINCT FROM (to_jsonb(OLD)-'unit_reviewed'-'reviewed_current_unit_id') THEN
 RAISE EXCEPTION 'procurement line snapshot is immutable'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER procurement_line_frozen BEFORE UPDATE OR DELETE ON stores_procurementline FOR EACH ROW EXECUTE FUNCTION procurement_frozen_line();
"""
REVERSE="""
DROP TRIGGER procurement_line_frozen ON stores_procurementline;
DROP FUNCTION procurement_frozen_line();
DROP TRIGGER procurement_event_immutable ON stores_procurementevent;
DROP FUNCTION procurement_immutable_event();
"""


class Migration(migrations.Migration):
    dependencies=[('stores','0007_procurement_settings_singleton')]
    operations=[migrations.RunSQL(FORWARD,REVERSE)]
