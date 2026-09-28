"""Immutable amount components and explicit zero cash approval evidence."""
import importlib
from django.db import migrations
OLD=importlib.import_module("aftersales.migrations.0005_return_guards").FUNDS
FUNDS=OLD.replace('LEFT JOIN aftersale_return_acceptance a ON a.case_id=c.id','LEFT JOIN aftersale_return_acceptance a ON a.case_id=c.id\n    LEFT JOIN aftersale_refund_amount_snapshot s ON s.case_id=c.id')
FUNDS=FUNDS.replace('i.amount_fen=COALESCE(a.refund_amount_fen,c.amount_fen)','i.amount_fen=COALESCE(s.total_fen,a.refund_amount_fen,c.amount_fen)')
FUNDS=FUNDS.replace("  IF (case_state='COMPLETED'", "  IF case_state='COMPLETED' AND intent_state IS NULL AND EXISTS(\n    SELECT 1 FROM aftersale_benefit_settlement b JOIN aftersale_refund_amount_snapshot s ON s.case_id=b.case_id\n    JOIN aftersale_case c ON c.id=b.case_id JOIN customer_order_line l ON l.id=c.order_line_id\n    JOIN customer_order o ON o.id=l.order_id\n    WHERE b.case_id=cid AND s.total_fen=0 AND o.status='PAID' AND b.expected_revision+1=c.revision)\n  THEN RETURN NULL; END IF;\n  IF (case_state='COMPLETED'")
SQL=r"""
CREATE TRIGGER refund_amount_immutable BEFORE UPDATE OR DELETE ON aftersale_refund_amount_snapshot
FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE TRIGGER shipping_refund_immutable BEFORE UPDATE OR DELETE ON aftersale_shipping_refund_claim
FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE TRIGGER benefit_settlement_immutable BEFORE UPDATE OR DELETE ON aftersale_benefit_settlement
FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE FUNCTION d3_refund_amount_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c aftersale_case%ROWTYPE; o customer_order%ROWTYPE; goods bigint;
BEGIN
 SELECT * INTO c FROM aftersale_case WHERE id=NEW.case_id;
 SELECT ord.* INTO o FROM customer_order ord JOIN customer_order_line l ON l.order_id=ord.id WHERE l.id=c.order_line_id;
 IF c.status<>'WAITING_REFUND' OR o.status<>'PAID' THEN RAISE EXCEPTION 'refund components require approved paid case'; END IF;
 IF TG_TABLE_NAME='aftersale_shipping_refund_claim' THEN
  IF NEW.order_id IS DISTINCT FROM o.id OR NEW.amount_fen IS DISTINCT FROM o.shipping_fee_fen OR
   NOT EXISTS(SELECT 1 FROM customer_order_line WHERE id=c.order_line_id AND fulfillment_kind='SHIP') OR
   EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=o.id) OR
   EXISTS(SELECT 1 FROM customer_order_line l WHERE l.order_id=o.id AND l.fulfillment_kind='SHIP' AND
     l.quantity<>(SELECT COALESCE(SUM(COALESCE(a.refund_quantity,f.quantity)),0) FROM aftersale_case f
       LEFT JOIN aftersale_return_acceptance a ON a.case_id=f.id
       WHERE f.order_line_id=l.id AND f.status IN ('WAITING_REFUND','COMPLETED')))
  THEN RAISE EXCEPTION 'shipping requires all unshipped lines approved once'; END IF;
 ELSIF TG_TABLE_NAME='aftersale_refund_amount_snapshot' THEN
  SELECT COALESCE((SELECT refund_amount_fen FROM aftersale_return_acceptance WHERE case_id=c.id),c.amount_fen) INTO goods;
  IF NEW.goods_fen IS DISTINCT FROM goods OR NEW.shipping_fen<>COALESCE((SELECT amount_fen FROM aftersale_shipping_refund_claim WHERE case_id=c.id),0) OR NEW.total_fen>o.payable_fen
  THEN RAISE EXCEPTION 'refund components must match approved original snapshot'; END IF;
 ELSE
  IF NEW.expected_revision<>c.revision OR EXISTS(SELECT 1 FROM refund_intent WHERE case_id=c.id) OR
    NOT EXISTS(SELECT 1 FROM aftersale_refund_amount_snapshot WHERE case_id=c.id AND total_fen=0)
  THEN RAISE EXCEPTION 'benefit completion cannot replace funds'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER refund_amount_insert_guard BEFORE INSERT ON aftersale_refund_amount_snapshot
FOR EACH ROW EXECUTE FUNCTION d3_refund_amount_guard();
CREATE TRIGGER shipping_refund_insert_guard BEFORE INSERT ON aftersale_shipping_refund_claim
FOR EACH ROW EXECUTE FUNCTION d3_refund_amount_guard();
CREATE TRIGGER benefit_settlement_insert_guard BEFORE INSERT ON aftersale_benefit_settlement
FOR EACH ROW EXECUTE FUNCTION d3_refund_amount_guard();
CREATE FUNCTION check_d3_settlement_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='aftersale_benefit_settlement' AND NOT EXISTS(SELECT 1 FROM aftersale_case WHERE id=NEW.case_id AND status='COMPLETED')
 THEN RAISE EXCEPTION 'benefit settlement requires atomic completed case'; END IF;
 IF TG_TABLE_NAME='aftersale_shipping_refund_claim' THEN
  IF NOT EXISTS(SELECT 1 FROM aftersale_refund_amount_snapshot WHERE case_id=NEW.case_id AND shipping_fen=NEW.amount_fen)
  THEN RAISE EXCEPTION 'shipping claim requires frozen amount components'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER shipping_claim_components AFTER INSERT ON aftersale_shipping_refund_claim
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_d3_settlement_evidence();
CREATE CONSTRAINT TRIGGER benefit_settlement_final AFTER INSERT ON aftersale_benefit_settlement
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_d3_settlement_evidence();
"""
REVERSE="""
DROP TRIGGER shipping_claim_components ON aftersale_shipping_refund_claim;
DROP TRIGGER benefit_settlement_final ON aftersale_benefit_settlement;
DROP FUNCTION check_d3_settlement_evidence();
DROP TRIGGER benefit_settlement_insert_guard ON aftersale_benefit_settlement;
DROP TRIGGER shipping_refund_insert_guard ON aftersale_shipping_refund_claim;
DROP TRIGGER refund_amount_insert_guard ON aftersale_refund_amount_snapshot;
DROP FUNCTION d3_refund_amount_guard();
DROP TRIGGER benefit_settlement_immutable ON aftersale_benefit_settlement;
DROP TRIGGER shipping_refund_immutable ON aftersale_shipping_refund_claim;
DROP TRIGGER refund_amount_immutable ON aftersale_refund_amount_snapshot;
"""
class Migration(migrations.Migration):
    dependencies=[('aftersales','0006_benefitonlysettlement_ordershippingrefundclaim_and_more'),('payments','0018_wechat_refund_notice_guards')]
    operations=[migrations.RunSQL(FUNDS+SQL,REVERSE+OLD)]
