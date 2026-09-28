from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("benefits", "0001_initial")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE FUNCTION benefit_points_event_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'points event is immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER benefit_points_event_immutable
          BEFORE UPDATE OR DELETE ON benefit_points_event
          FOR EACH ROW EXECUTE FUNCTION benefit_points_event_reject_mutation();
        """,
        reverse_sql="""
        DROP TRIGGER benefit_points_event_immutable ON benefit_points_event;
        DROP FUNCTION benefit_points_event_reject_mutation();
        """,
    )]
