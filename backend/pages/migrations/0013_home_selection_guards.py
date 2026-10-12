"""Retain the singleton, epoch and successful home-selection receipts."""
from django.db import migrations


FORWARD = """
CREATE FUNCTION protect_home_selection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Home identity singleton cannot be removed' USING ERRCODE = '23514';
    END IF;
    IF NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'Home identity changes require the next revision' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM page_publication WHERE page_id = NEW.page_id AND current_version_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Home identity requires a published page' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER home_selection_guard BEFORE UPDATE OR DELETE ON home_selection
FOR EACH ROW EXECUTE FUNCTION protect_home_selection();
CREATE TRIGGER home_selection_receipt_immutable BEFORE UPDATE OR DELETE ON home_selection_request
FOR EACH ROW EXECUTE FUNCTION protect_content_version();
"""
REVERSE = """
DROP TRIGGER home_selection_receipt_immutable ON home_selection_request;
DROP TRIGGER home_selection_guard ON home_selection;
DROP FUNCTION protect_home_selection();
"""


class Migration(migrations.Migration):
    dependencies = [('pages', '0012_seed_home_selection')]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
