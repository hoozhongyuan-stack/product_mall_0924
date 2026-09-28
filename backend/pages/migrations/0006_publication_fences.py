"""Publication counters and successful operation receipts cannot be rewritten."""
from django.db import migrations

FORWARD = """
CREATE FUNCTION protect_publication_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.revision <> (CASE WHEN NEW.current_version_id IS NULL THEN 0 ELSE 1 END) THEN
            RAISE EXCEPTION 'Invalid initial publication revision' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN
        IF NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'Publication changes require the next revision' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.revision <> OLD.revision THEN
        RAISE EXCEPTION 'Publication revision cannot change without its pointer' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER page_publication_revision BEFORE INSERT OR UPDATE ON page_publication
FOR EACH ROW EXECUTE FUNCTION protect_publication_revision();
CREATE TRIGGER startup_publication_revision BEFORE INSERT OR UPDATE ON startup_publication
FOR EACH ROW EXECUTE FUNCTION protect_publication_revision();
CREATE TRIGGER page_publish_receipt_immutable BEFORE UPDATE OR DELETE ON page_publish_request
FOR EACH ROW EXECUTE FUNCTION protect_content_version();
CREATE TRIGGER startup_publish_receipt_immutable BEFORE UPDATE OR DELETE ON startup_publish_request
FOR EACH ROW EXECUTE FUNCTION protect_content_version();
CREATE TRIGGER rollback_receipt_immutable BEFORE UPDATE OR DELETE ON content_rollback_request
FOR EACH ROW EXECUTE FUNCTION protect_content_version();
"""
REVERSE = """
DROP TRIGGER rollback_receipt_immutable ON content_rollback_request;
DROP TRIGGER startup_publish_receipt_immutable ON startup_publish_request;
DROP TRIGGER page_publish_receipt_immutable ON page_publish_request;
DROP TRIGGER startup_publication_revision ON startup_publication;
DROP TRIGGER page_publication_revision ON page_publication;
DROP FUNCTION protect_publication_revision();
"""


class Migration(migrations.Migration):
    dependencies = [("pages", "0005_content_history")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
