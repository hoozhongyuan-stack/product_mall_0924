from django.db import migrations
FORWARD="""
CREATE FUNCTION guard_offline_refund() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE i refund_intent%ROWTYPE;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'offline refund records cannot be deleted'; END IF;
 SELECT * INTO i FROM refund_intent WHERE id=NEW.intent_id;
 IF i.channel!='OFFLINE' OR i.created_by_id!=NEW.prepared_by_id OR i.merchant_account_id!=NEW.merchant_account_id OR i.amount_fen!=NEW.amount_fen THEN RAISE EXCEPTION 'offline refund identity mismatch'; END IF;
 IF NEW.authorized_by_id IS NOT NULL AND NEW.authorized_by_id=NEW.prepared_by_id THEN RAISE EXCEPTION 'offline refund dual control required'; END IF;
 IF NEW.outcome='SUCCEEDED' AND i.status!='SUCCEEDED' THEN RAISE EXCEPTION 'offline refund success needs settled funds'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(NEW)-ARRAY['authorized_by_id','authorized_at','outcome']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['authorized_by_id','authorized_at','outcome']) THEN RAISE EXCEPTION 'offline refund facts are immutable'; END IF;
  IF OLD.authorized_at IS NOT NULL AND (NEW.authorized_at IS DISTINCT FROM OLD.authorized_at OR NEW.authorized_by_id IS DISTINCT FROM OLD.authorized_by_id) THEN RAISE EXCEPTION 'offline refund authorization is immutable'; END IF;
  IF OLD.outcome='SUCCEEDED' AND NEW.outcome!='SUCCEEDED' THEN RAISE EXCEPTION 'offline refund success is immutable'; END IF;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER offline_refund_guard BEFORE INSERT OR UPDATE OR DELETE ON offline_refund_reconciliation FOR EACH ROW EXECUTE FUNCTION guard_offline_refund();
"""
class Migration(migrations.Migration):
 dependencies=[('payments','0013_offlinerefundreconciliation')]
 operations=[migrations.RunSQL(FORWARD,"DROP TRIGGER offline_refund_guard ON offline_refund_reconciliation; DROP FUNCTION guard_offline_refund();")]
