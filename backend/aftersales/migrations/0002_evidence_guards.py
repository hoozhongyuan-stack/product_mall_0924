from django.db import migrations

FORWARD = """
INSERT INTO aftersale_policy(id, received_window_days, revision) VALUES (1,15,1);
CREATE FUNCTION aftersale_immutable() RETURNS trigger AS $$ BEGIN
 RAISE EXCEPTION 'after-sale evidence is immutable';
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER aftersale_snapshot_immutable BEFORE UPDATE OR DELETE ON aftersale_order_snapshot
 FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE TRIGGER aftersale_event_immutable BEFORE UPDATE OR DELETE ON aftersale_event
 FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE FUNCTION aftersale_case_guard() RETURNS trigger AS $$
DECLARE owner_id uuid; purchased integer;
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'after-sale case cannot be deleted'; END IF;
 SELECT o.member_id,l.quantity INTO owner_id,purchased FROM customer_order_line l
 JOIN customer_order o ON o.id=l.order_id WHERE l.id=NEW.order_line_id;
 IF owner_id IS DISTINCT FROM NEW.member_id OR NEW.quantity > purchased THEN
  RAISE EXCEPTION 'case identity or quantity mismatch'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.order_line_id,NEW.member_id,NEW.request_key,NEW.request_digest,NEW.quantity,NEW.amount_fen,
      NEW.used_quantity,NEW.unredeemed_quantity,NEW.kind,NEW.reason,NEW.created_at)
     IS DISTINCT FROM
     (OLD.order_line_id,OLD.member_id,OLD.request_key,OLD.request_digest,OLD.quantity,OLD.amount_fen,
      OLD.used_quantity,OLD.unredeemed_quantity,OLD.kind,OLD.reason,OLD.created_at) THEN
   RAISE EXCEPTION 'case request is immutable'; END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
   (OLD.status='PENDING_REVIEW' AND NEW.status IN ('WAITING_RETURN','WAITING_REFUND','REJECTED','WITHDRAWN')) OR
   (OLD.status='WAITING_REFUND' AND NEW.status='COMPLETED')) THEN
   RAISE EXCEPTION 'invalid after-sale transition'; END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER aftersale_case_guard BEFORE INSERT OR UPDATE OR DELETE ON aftersale_case
 FOR EACH ROW EXECUTE FUNCTION aftersale_case_guard();
CREATE FUNCTION aftersale_allocation_guard() RETURNS trigger AS $$
DECLARE purchased integer; payable bigint;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'allocation cannot be deleted'; END IF;
 SELECT quantity,payable_fen INTO purchased,payable FROM customer_order_line WHERE id=NEW.order_line_id;
 IF NEW.purchased_qty IS DISTINCT FROM purchased OR NEW.payable_fen IS DISTINCT FROM payable THEN
 RAISE EXCEPTION 'allocation must match immutable order snapshot'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER aftersale_allocation_guard BEFORE INSERT OR UPDATE OR DELETE ON aftersale_allocation
 FOR EACH ROW EXECUTE FUNCTION aftersale_allocation_guard();
CREATE FUNCTION aftersale_check_aggregate() RETURNS trigger AS $$
DECLARE line_id uuid; a aftersale_allocation%ROWTYPE; rq bigint; rf bigint; cq bigint; cf bigint;
BEGIN
 line_id := NEW.order_line_id;
 SELECT * INTO a FROM aftersale_allocation WHERE order_line_id=line_id;
 SELECT COALESCE(SUM(quantity) FILTER (WHERE status IN ('PENDING_REVIEW','WAITING_RETURN','WAITING_REFUND')),0),
 COALESCE(SUM(amount_fen) FILTER (WHERE status IN ('PENDING_REVIEW','WAITING_RETURN','WAITING_REFUND')),0),
 COALESCE(SUM(quantity) FILTER (WHERE status='COMPLETED'),0),
 COALESCE(SUM(amount_fen) FILTER (WHERE status='COMPLETED'),0)
 INTO rq,rf,cq,cf FROM aftersale_case WHERE order_line_id=line_id;
 IF a.order_line_id IS NULL OR (a.reserved_qty,a.reserved_fen,a.refunded_qty,a.refunded_fen)
    IS DISTINCT FROM (rq,rf,cq,cf) THEN
  RAISE EXCEPTION 'after-sale allocation does not match cases'; END IF;
 RETURN NULL;
END; $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER aftersale_cases_aggregate AFTER INSERT OR UPDATE ON aftersale_case
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION aftersale_check_aggregate();
CREATE CONSTRAINT TRIGGER aftersale_allocation_aggregate AFTER INSERT OR UPDATE ON aftersale_allocation
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION aftersale_check_aggregate();
"""
REVERSE = """
DROP TRIGGER aftersale_cases_aggregate ON aftersale_case;
DROP TRIGGER aftersale_allocation_aggregate ON aftersale_allocation;
DROP FUNCTION aftersale_check_aggregate();
DROP TRIGGER aftersale_allocation_guard ON aftersale_allocation;
DROP FUNCTION aftersale_allocation_guard();
DROP TRIGGER aftersale_case_guard ON aftersale_case;
DROP FUNCTION aftersale_case_guard();
DROP TRIGGER aftersale_snapshot_immutable ON aftersale_order_snapshot;
DROP TRIGGER aftersale_event_immutable ON aftersale_event;
DROP FUNCTION aftersale_immutable();
DELETE FROM aftersale_policy;
"""
class Migration(migrations.Migration):
    dependencies = [("aftersales", "0001_initial")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
