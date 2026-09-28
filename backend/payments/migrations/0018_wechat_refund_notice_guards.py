from django.db import migrations

SQL = """
CREATE FUNCTION protect_wechat_refund_notice() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'refund notification facts cannot be deleted'; END IF;
  IF (NEW.id, NEW.intent_id, NEW.merchant_id, NEW.event_id, NEW.digest, NEW.provider_status, NEW.recorded_at)
     IS DISTINCT FROM
     (OLD.id, OLD.intent_id, OLD.merchant_id, OLD.event_id, OLD.digest, OLD.provider_status, OLD.recorded_at)
     OR (OLD.conflict AND NOT NEW.conflict)
     OR (OLD.processed_at IS NOT NULL AND NEW.processed_at IS DISTINCT FROM OLD.processed_at)
  THEN RAISE EXCEPTION 'refund notification facts are immutable'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER wechat_refund_notice_guard BEFORE UPDATE OR DELETE ON wechat_refund_notice
FOR EACH ROW EXECUTE FUNCTION protect_wechat_refund_notice();
CREATE FUNCTION protect_wechat_refund_conflict() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'refund conflict facts are immutable'; END $$;
CREATE TRIGGER wechat_refund_conflict_guard BEFORE UPDATE OR DELETE ON wechat_refund_conflict
FOR EACH ROW EXECUTE FUNCTION protect_wechat_refund_conflict();
"""
REVERSE = """
DROP TRIGGER wechat_refund_conflict_guard ON wechat_refund_conflict;
DROP FUNCTION protect_wechat_refund_conflict();
DROP TRIGGER wechat_refund_notice_guard ON wechat_refund_notice;
DROP FUNCTION protect_wechat_refund_notice();
"""


class Migration(migrations.Migration):
    dependencies = [("payments", "0017_wechat_refund_conflict")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
