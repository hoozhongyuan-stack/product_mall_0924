from django.db import migrations

SQL = """
CREATE FUNCTION benefit_coupon_campaign_guard() RETURNS trigger AS $$
BEGIN
 IF OLD.status = 'PUBLISHED' THEN
  IF ROW(NEW.code,NEW.title,NEW.kind,NEW.min_goods_fen,NEW.discount_fen,NEW.product_ids,
         NEW.redeem_eligible,NEW.valid_from,NEW.valid_until,NEW.active,NEW.total_quantity,
         NEW.self_claim_limit,NEW.claim_mode,NEW.status)
     IS DISTINCT FROM
     ROW(OLD.code,OLD.title,OLD.kind,OLD.min_goods_fen,OLD.discount_fen,OLD.product_ids,
         OLD.redeem_eligible,OLD.valid_from,OLD.valid_until,OLD.active,OLD.total_quantity,
         OLD.self_claim_limit,OLD.claim_mode,OLD.status) THEN
   RAISE EXCEPTION 'published coupon terms are immutable';
  END IF;
  IF NEW.issued_quantity < OLD.issued_quantity THEN
   RAISE EXCEPTION 'coupon issuance quota cannot be restored';
  END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER benefit_coupon_campaign_guard BEFORE UPDATE ON benefit_coupon_campaign
 FOR EACH ROW EXECUTE FUNCTION benefit_coupon_campaign_guard();
CREATE FUNCTION benefit_coupon_allocation_guard() RETURNS trigger AS $$
DECLARE allowed integer;
BEGIN
 SELECT self_claim_limit INTO allowed FROM benefit_coupon_campaign WHERE id=NEW.campaign_id;
 IF NEW.self_count > allowed THEN RAISE EXCEPTION 'coupon self claim limit exceeded'; END IF;
 IF TG_OP='UPDATE' AND (NEW.self_count < OLD.self_count OR NEW.admin_count < OLD.admin_count OR
    NEW.member_id IS DISTINCT FROM OLD.member_id OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id) THEN
  RAISE EXCEPTION 'coupon allocations cannot be reduced or moved';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER benefit_coupon_allocation_guard BEFORE INSERT OR UPDATE ON benefit_coupon_allocation
 FOR EACH ROW EXECUTE FUNCTION benefit_coupon_allocation_guard();
CREATE FUNCTION benefit_coupon_entitlement_guard() RETURNS trigger AS $$
DECLARE campaign_state text; proof benefit_coupon_issuance%ROWTYPE;
BEGIN
 IF TG_OP='UPDATE' AND ROW(NEW.member_id,NEW.campaign_id,NEW.issuance_id,NEW.issued_at)
      IS DISTINCT FROM ROW(OLD.member_id,OLD.campaign_id,OLD.issuance_id,OLD.issued_at) THEN
   RAISE EXCEPTION 'coupon owner and issuance are immutable';
 END IF;
 SELECT status INTO campaign_state FROM benefit_coupon_campaign WHERE id=NEW.campaign_id;
 IF campaign_state='DRAFT' THEN RAISE EXCEPTION 'draft coupon cannot be granted'; END IF;
 IF campaign_state='PUBLISHED' THEN
  SELECT * INTO proof FROM benefit_coupon_issuance WHERE id=NEW.issuance_id;
  IF proof.id IS NULL OR proof.member_id != NEW.member_id OR proof.campaign_id != NEW.campaign_id
     OR NOT COALESCE(proof.result->'couponIds' @> to_jsonb(ARRAY[NEW.id::text]),FALSE) THEN
   RAISE EXCEPTION 'coupon issuance proof required';
  END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER benefit_coupon_entitlement_guard BEFORE INSERT OR UPDATE ON benefit_member_coupon
 FOR EACH ROW EXECUTE FUNCTION benefit_coupon_entitlement_guard();
CREATE FUNCTION benefit_coupon_distribution_final_guard() RETURNS trigger AS $$
DECLARE campaign_uuid uuid; member_uuid uuid; campaign_state text; total integer; count_claim integer; count_admin integer;
BEGIN
 campaign_uuid := NEW.campaign_id;
 SELECT status,issued_quantity INTO campaign_state,total FROM benefit_coupon_campaign WHERE id=campaign_uuid;
 IF campaign_state='PUBLISHED' AND total != COALESCE((SELECT SUM(quantity) FROM benefit_coupon_issuance WHERE campaign_id=campaign_uuid),0) THEN
  RAISE EXCEPTION 'coupon issuance counter must match receipts';
 END IF;
 IF TG_TABLE_NAME='benefit_coupon_issuance' THEN
  IF jsonb_typeof(NEW.result->'couponIds') IS DISTINCT FROM 'array' OR
     jsonb_array_length(NEW.result->'couponIds') != NEW.quantity OR
     (SELECT COUNT(DISTINCT value) FROM jsonb_array_elements_text(NEW.result->'couponIds')) != NEW.quantity OR
     (SELECT COUNT(*) FROM benefit_member_coupon WHERE issuance_id=NEW.id) != NEW.quantity THEN
    RAISE EXCEPTION 'coupon issuance receipt must match entitlements';
  END IF;
 END IF;
 member_uuid := NEW.member_id;
 SELECT COALESCE(SUM(quantity) FILTER (WHERE kind='SELF'),0),COALESCE(SUM(quantity) FILTER (WHERE kind='ADMIN'),0)
 INTO count_claim,count_admin FROM benefit_coupon_issuance WHERE campaign_id=campaign_uuid AND member_id=member_uuid;
 IF NOT EXISTS(SELECT 1 FROM benefit_coupon_allocation WHERE campaign_id=campaign_uuid AND member_id=member_uuid
                  AND self_count=count_claim AND admin_count=count_admin) THEN
  RAISE EXCEPTION 'coupon member counters must match receipts';
 END IF;
 RETURN NULL;
END; $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER benefit_coupon_issuance_final_guard AFTER INSERT ON benefit_coupon_issuance
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION benefit_coupon_distribution_final_guard();
CREATE CONSTRAINT TRIGGER benefit_coupon_allocation_final_guard AFTER INSERT OR UPDATE ON benefit_coupon_allocation
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION benefit_coupon_distribution_final_guard();
CREATE FUNCTION benefit_coupon_counter_final_guard() RETURNS trigger AS $$
DECLARE current_total integer; campaign_state text;
BEGIN
 SELECT status,issued_quantity INTO campaign_state,current_total FROM benefit_coupon_campaign WHERE id=NEW.id;
 IF campaign_state='PUBLISHED' AND current_total != COALESCE((SELECT SUM(quantity) FROM benefit_coupon_issuance WHERE campaign_id=NEW.id),0) THEN
  RAISE EXCEPTION 'coupon issuance counter must match receipts';
 END IF;
 RETURN NULL;
END; $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER benefit_coupon_counter_final_guard AFTER UPDATE ON benefit_coupon_campaign
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION benefit_coupon_counter_final_guard();
CREATE FUNCTION benefit_coupon_reject_entitlement_delete() RETURNS trigger AS $$
BEGIN
 IF OLD.issuance_id IS NOT NULL THEN RAISE EXCEPTION 'issued coupon proof cannot be deleted'; END IF;
 RETURN OLD;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER benefit_coupon_entitlement_delete_guard BEFORE DELETE ON benefit_member_coupon
 FOR EACH ROW EXECUTE FUNCTION benefit_coupon_reject_entitlement_delete();
CREATE TRIGGER benefit_coupon_issuance_immutable BEFORE UPDATE OR DELETE ON benefit_coupon_issuance
 FOR EACH ROW EXECUTE FUNCTION benefit_points_event_reject_mutation();
CREATE TRIGGER benefit_coupon_operation_immutable BEFORE UPDATE OR DELETE ON benefit_coupon_operation
 FOR EACH ROW EXECUTE FUNCTION benefit_points_event_reject_mutation();
"""
REVERSE = """
DROP TRIGGER benefit_coupon_entitlement_delete_guard ON benefit_member_coupon;
DROP FUNCTION benefit_coupon_reject_entitlement_delete();
DROP TRIGGER benefit_coupon_counter_final_guard ON benefit_coupon_campaign;
DROP FUNCTION benefit_coupon_counter_final_guard();
DROP TRIGGER benefit_coupon_allocation_final_guard ON benefit_coupon_allocation;
DROP TRIGGER benefit_coupon_issuance_final_guard ON benefit_coupon_issuance;
DROP FUNCTION benefit_coupon_distribution_final_guard();
DROP TRIGGER benefit_coupon_operation_immutable ON benefit_coupon_operation;
DROP TRIGGER benefit_coupon_issuance_immutable ON benefit_coupon_issuance;
DROP TRIGGER benefit_coupon_entitlement_guard ON benefit_member_coupon;
DROP FUNCTION benefit_coupon_entitlement_guard();
DROP TRIGGER benefit_coupon_allocation_guard ON benefit_coupon_allocation;
DROP FUNCTION benefit_coupon_allocation_guard();
DROP TRIGGER benefit_coupon_campaign_guard ON benefit_coupon_campaign;
DROP FUNCTION benefit_coupon_campaign_guard();
"""

class Migration(migrations.Migration):
    dependencies=[('benefits','0012_couponallocation_couponissuance_couponoperation_and_more')]
    operations=[migrations.RunSQL(SQL,REVERSE)]
