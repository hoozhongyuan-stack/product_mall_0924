from django.db import migrations

SQL = r"""
CREATE FUNCTION guard_refund_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'refund evidence cannot be deleted'; END IF;
  IF TG_TABLE_NAME = 'refund_intent' THEN
    IF (to_jsonb(NEW) - ARRAY['status','active_operation_id','lease_until','succeeded_at'])
       IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','active_operation_id','lease_until','succeeded_at'])
       OR (OLD.status = 'SUCCEEDED' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD))
    THEN RAISE EXCEPTION 'refund intent identity or success is immutable'; END IF;
  ELSIF TG_TABLE_NAME = 'refund_evidence' THEN
    IF (to_jsonb(NEW) - 'applied_at') IS DISTINCT FROM (to_jsonb(OLD) - 'applied_at')
       OR (OLD.applied_at IS NOT NULL AND NEW.applied_at IS DISTINCT FROM OLD.applied_at)
    THEN RAISE EXCEPTION 'refund funds evidence is immutable'; END IF;
  ELSIF TG_TABLE_NAME = 'refund_operation' THEN
    IF (to_jsonb(NEW) - ARRAY['outcome','finished_at','failure_code'])
       IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['outcome','finished_at','failure_code'])
       OR (OLD.finished_at IS NOT NULL AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD))
    THEN RAISE EXCEPTION 'refund operation history is immutable'; END IF;
  ELSE
    RAISE EXCEPTION 'refund history is immutable';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER refund_intent_immutable BEFORE UPDATE OR DELETE ON refund_intent
FOR EACH ROW EXECUTE FUNCTION guard_refund_immutable();
CREATE TRIGGER refund_evidence_immutable BEFORE UPDATE OR DELETE ON refund_evidence
FOR EACH ROW EXECUTE FUNCTION guard_refund_immutable();
CREATE TRIGGER refund_operation_immutable BEFORE UPDATE OR DELETE ON refund_operation
FOR EACH ROW EXECUTE FUNCTION guard_refund_immutable();
CREATE TRIGGER refund_notification_immutable BEFORE UPDATE OR DELETE ON refund_notification
FOR EACH ROW EXECUTE FUNCTION guard_refund_immutable();
CREATE TRIGGER refund_history_immutable BEFORE UPDATE OR DELETE ON refund_history
FOR EACH ROW EXECUTE FUNCTION guard_refund_immutable();

CREATE FUNCTION check_refund_final_state() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cid uuid; case_state text; intent_state text; matched integer;
BEGIN
  IF TG_TABLE_NAME = 'aftersale_case' THEN cid := NEW.id;
  ELSIF TG_TABLE_NAME = 'refund_intent' THEN cid := NEW.case_id;
  ELSE SELECT case_id INTO cid FROM refund_intent WHERE id = NEW.intent_id;
  END IF;
  SELECT status INTO case_state FROM aftersale_case WHERE id=cid;
  SELECT status INTO intent_state FROM refund_intent WHERE case_id=cid;
  SELECT count(*) INTO matched FROM refund_evidence e
    JOIN refund_intent i ON i.id=e.intent_id
    JOIN aftersale_case c ON c.id=i.case_id
    JOIN customer_order_line l ON l.id=c.order_line_id
    JOIN payment_receipt p ON p.id=i.receipt_id
    WHERE c.id=cid AND e.applied_at IS NOT NULL AND i.status='SUCCEEDED'
      AND c.status='COMPLETED' AND p.applied_at IS NOT NULL AND p.order_id=l.order_id
      AND i.channel=p.channel AND i.merchant_account_id=p.merchant_account_id
      AND i.original_trade_no=p.external_trade_no AND i.amount_fen=c.amount_fen
      AND e.channel=i.channel AND e.merchant_account_id=i.merchant_account_id
      AND e.original_trade_no=i.original_trade_no AND e.amount_fen=i.amount_fen
      AND (e.channel <> 'OFFLINE' OR e.confirmed_by_id <> i.created_by_id);
  IF (case_state='COMPLETED' OR intent_state='SUCCEEDED' OR
      EXISTS(SELECT 1 FROM refund_evidence e JOIN refund_intent i ON i.id=e.intent_id
             WHERE i.case_id=cid AND e.applied_at IS NOT NULL)) AND matched <> 1
  THEN RAISE EXCEPTION 'refund completion requires matching applied funds'; END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER refund_case_final AFTER INSERT OR UPDATE ON aftersale_case
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_refund_final_state();
CREATE CONSTRAINT TRIGGER refund_intent_final AFTER INSERT OR UPDATE ON refund_intent
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_refund_final_state();
CREATE CONSTRAINT TRIGGER refund_evidence_final AFTER INSERT OR UPDATE ON refund_evidence
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_refund_final_state();
"""

REVERSE = r"""
DROP TRIGGER refund_evidence_final ON refund_evidence;
DROP TRIGGER refund_intent_final ON refund_intent;
DROP TRIGGER refund_case_final ON aftersale_case;
DROP FUNCTION check_refund_final_state();
DROP TRIGGER refund_history_immutable ON refund_history;
DROP TRIGGER refund_notification_immutable ON refund_notification;
DROP TRIGGER refund_operation_immutable ON refund_operation;
DROP TRIGGER refund_evidence_immutable ON refund_evidence;
DROP TRIGGER refund_intent_immutable ON refund_intent;
DROP FUNCTION guard_refund_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("payments", "0010_refundevidence_refundintent_refundhistory_and_more"),
                    ("aftersales", "0003_aftersalecase_aftersale_case_money_positive"),
                    ("fulfillment", "0005_voucher_refund_event"),
                    ("inventory", "0009_remove_inventoryledger_ledger_direction_and_source_valid_and_more")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
