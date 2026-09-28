"""Preserve the exact source and private object identity of a code version."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("code_versions", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE FUNCTION mini_code_version_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'mini code versions are immutable';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER mini_code_version_no_update
                BEFORE UPDATE OR DELETE ON mini_code_version
                FOR EACH ROW EXECUTE FUNCTION mini_code_version_immutable();
            """,
            reverse_sql="""
            DROP TRIGGER IF EXISTS mini_code_version_no_update ON mini_code_version;
            DROP FUNCTION IF EXISTS mini_code_version_immutable();
            """,
        ),
    ]
