"""Retain publication epochs and prevent clearing a successful pointer."""
from django.db import migrations


FORWARD = """
CREATE FUNCTION protect_publication_retention() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Publication epochs cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF OLD.current_version_id IS NOT NULL AND NEW.current_version_id IS NULL THEN
        RAISE EXCEPTION 'A successful publication cannot be cleared' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER page_publication_retention BEFORE UPDATE OR DELETE ON page_publication
FOR EACH ROW EXECUTE FUNCTION protect_publication_retention();
CREATE TRIGGER startup_publication_retention BEFORE UPDATE OR DELETE ON startup_publication
FOR EACH ROW EXECUTE FUNCTION protect_publication_retention();
"""

REVERSE = """
DROP TRIGGER startup_publication_retention ON startup_publication;
DROP TRIGGER page_publication_retention ON page_publication;
DROP FUNCTION protect_publication_retention();
"""


class Migration(migrations.Migration):
    dependencies = [("pages", "0006_publication_fences")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
