"""Warehouse evidence precedes funds. Original application identity remains immutable."""
import importlib
import re
from django.db import migrations

OLD_CASE=importlib.import_module('aftersales.migrations.0002_evidence_guards').FORWARD
OLD_FUNDS=importlib.import_module('payments.migrations.0011_refund_evidence_guards').SQL
OLD_EFFECT=importlib.import_module('payments.migrations.0012_refund_domain_side_effects').SQL

def function(sql,name):
    pattern=r'CREATE FUNCTION '+re.escape(name)+r'.*?AS \$\$.*?\$\$(?: LANGUAGE plpgsql)?;'
    return re.search(pattern,sql,re.S).group(0)

CASE=function(OLD_CASE,'aftersale_case_guard()').replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)
CASE=CASE.replace("(OLD.status='WAITING_REFUND' AND NEW.status='COMPLETED'))", "(OLD.status='WAITING_REFUND' AND NEW.status='COMPLETED') OR\n   (OLD.status='WAITING_RETURN' AND NEW.status IN ('WAITING_REFUND','REJECTED') AND\n    EXISTS(SELECT 1 FROM aftersale_return_acceptance a WHERE a.case_id=NEW.id AND\n       ((NEW.status='WAITING_REFUND' AND a.refund_quantity>0) OR (NEW.status='REJECTED' AND a.refund_quantity=0)))))")
AGG=function(OLD_CASE,'aftersale_check_aggregate()').replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)
AGG=AGG.replace('SUM(quantity)', 'SUM(CASE WHEN status IN (\'WAITING_REFUND\',\'COMPLETED\') THEN COALESCE(r.refund_quantity,c.quantity) ELSE c.quantity END)').replace('SUM(amount_fen)', 'SUM(CASE WHEN status IN (\'WAITING_REFUND\',\'COMPLETED\') THEN COALESCE(r.refund_amount_fen,c.amount_fen) ELSE c.amount_fen END)')
AGG=AGG.replace('FROM aftersale_case WHERE order_line_id=line_id;', 'FROM aftersale_case c LEFT JOIN aftersale_return_acceptance r ON r.case_id=c.id WHERE c.order_line_id=line_id;')
FUNDS=function(OLD_FUNDS,'check_refund_final_state()').replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)
FUNDS=FUNDS.replace('JOIN aftersale_case c ON c.id=i.case_id','JOIN aftersale_case c ON c.id=i.case_id\n    LEFT JOIN aftersale_return_acceptance a ON a.case_id=c.id').replace('i.amount_fen=c.amount_fen','i.amount_fen=COALESCE(a.refund_amount_fen,c.amount_fen)')
EFFECT=function(OLD_EFFECT,'check_refund_domain_effects()').replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)
# RETURN has distinct columns and is governed by the return evidence trigger below.
EFFECT=EFFECT.replace("ELSE\n   IF EXISTS(SELECT 1 FROM inventory_ledger WHERE refund_case_id=cid)", "ELSIF l.fulfillment_kind='SHIP' AND c.kind='RETURN_REFUND' THEN\n   IF NOT EXISTS(SELECT 1 FROM aftersale_return_acceptance WHERE case_id=cid AND refund_quantity>0)\n   THEN RAISE EXCEPTION 'shipped refund requires accepted return or explicit waiver'; END IF;\n   IF EXISTS(SELECT 1 FROM inventory_ledger WHERE refund_case_id=cid)\n   THEN RAISE EXCEPTION 'accepted return cannot use unshipped restitution'; END IF;\n ELSE\n   IF EXISTS(SELECT 1 FROM inventory_ledger WHERE refund_case_id=cid)")

SQL=r"""
CREATE TRIGGER return_shipment_immutable BEFORE UPDATE OR DELETE ON aftersale_return_shipment
FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE TRIGGER return_acceptance_immutable BEFORE UPDATE OR DELETE ON aftersale_return_acceptance
FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE TRIGGER return_disposition_immutable BEFORE UPDATE OR DELETE ON inventory_return_disposition
FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE FUNCTION return_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c aftersale_case%ROWTYPE; l customer_order_line%ROWTYPE; alloc aftersale_allocation%ROWTYPE; maximum bigint;
BEGIN
 SELECT * INTO c FROM aftersale_case WHERE id=NEW.case_id;
 SELECT * INTO l FROM customer_order_line WHERE id=c.order_line_id;
 IF c.kind IS DISTINCT FROM 'RETURN_REFUND' OR c.status IS DISTINCT FROM 'WAITING_RETURN'
    OR c.revision IS DISTINCT FROM NEW.expected_revision OR l.fulfillment_kind IS DISTINCT FROM 'SHIP'
 THEN RAISE EXCEPTION 'return requires matching current return case'; END IF;
 IF TG_TABLE_NAME='aftersale_return_shipment' THEN
  IF c.member_id IS DISTINCT FROM NEW.member_id OR NEW.revision<>NEW.expected_revision+1
  THEN RAISE EXCEPTION 'return shipment owner or revision mismatch'; END IF;
 ELSE
  SELECT * INTO alloc FROM aftersale_allocation WHERE order_line_id=l.id;
  maximum := CASE WHEN NEW.refund_quantity=0 THEN 0 ELSE LEAST(c.amount_fen,GREATEST(0,FLOOR(l.payable_fen::numeric*(alloc.refunded_qty+NEW.refund_quantity)/l.quantity)-alloc.refunded_fen)) END;
  IF NEW.max_refund_amount_fen IS DISTINCT FROM maximum THEN RAISE EXCEPTION 'refund maximum must match snapshot allocation'; END IF;
  IF NEW.received_quantity>c.quantity OR NEW.refund_quantity>c.quantity OR NEW.max_refund_amount_fen>c.amount_fen
  THEN RAISE EXCEPTION 'return acceptance exceeds application'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER return_shipment_request BEFORE INSERT ON aftersale_return_shipment
FOR EACH ROW EXECUTE FUNCTION return_request_guard();
CREATE TRIGGER return_acceptance_request BEFORE INSERT ON aftersale_return_acceptance
FOR EACH ROW EXECUTE FUNCTION return_request_guard();

CREATE FUNCTION check_return_effects() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cid uuid; a aftersale_return_acceptance%ROWTYPE; d inventory_return_disposition%ROWTYPE;
 l customer_order_line%ROWTYPE; c aftersale_case%ROWTYPE; received bigint;
BEGIN
 IF TG_TABLE_NAME='inventory_ledger' THEN
  IF NEW.movement_type<>'RETURN' THEN RETURN NULL; END IF;
  cid:=NEW.return_case_id;
 ELSIF TG_TABLE_NAME='aftersale_case' THEN
  IF NEW.kind<>'RETURN_REFUND' THEN RETURN NULL; END IF;
  cid:=NEW.id;
 ELSE cid:=NEW.case_id; END IF;
 SELECT * INTO a FROM aftersale_return_acceptance WHERE case_id=cid;
 IF NOT FOUND THEN
  IF TG_TABLE_NAME<>'aftersale_case' THEN RAISE EXCEPTION 'return requires acceptance'; END IF;
  RETURN NULL;
 END IF;
 SELECT * INTO c FROM aftersale_case WHERE id=cid;
 SELECT * INTO l FROM customer_order_line WHERE id=c.order_line_id;
 IF (a.refund_quantity>0 AND c.status NOT IN ('WAITING_REFUND','COMPLETED')) OR
    (a.refund_quantity=0 AND c.status<>'REJECTED')
 THEN RAISE EXCEPTION 'acceptance requires final return decision state'; END IF;
 IF NOT EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id) OR
    NOT EXISTS(SELECT 1 FROM inventory_ledger g JOIN inventory_reservation r ON r.order_line_id=g.order_line_id
     JOIN inventory_balance b ON b.id=r.balance_id
     WHERE g.order_line_id=l.id AND g.movement_type='SALE' AND r.status='CONSUMED'
       AND r.base_quantity=l.base_quantity AND g.delta_base_units=-l.base_quantity
       AND g.warehouse_id=l.warehouse_id AND g.sku_id=l.sku_id AND g.unit_version_id=l.unit_version_id
       AND g.ratio=l.ratio AND b.warehouse_id=l.warehouse_id AND b.sku_id=l.sku_id)
 THEN RAISE EXCEPTION 'return requires original shipment and consumed stock'; END IF;
 SELECT * INTO d FROM inventory_return_disposition WHERE case_id=cid;
 IF d.id IS NULL OR (d.acceptance_id,d.order_line_id,d.received_quantity,d.salable_quantity,d.damaged_quantity,d.actor_id,d.reason)
  IS DISTINCT FROM (a.id,l.id,a.received_quantity,a.salable_quantity,a.received_quantity-a.salable_quantity,a.actor_id,a.reason)
 THEN RAISE EXCEPTION 'return requires matching disposition'; END IF;
 SELECT COALESCE(SUM(received_quantity),0) INTO received FROM inventory_return_disposition WHERE order_line_id=l.id;
 IF received>l.quantity THEN RAISE EXCEPTION 'returned quantity exceeds purchase'; END IF;
 IF a.salable_quantity>0 THEN
  IF NOT EXISTS(SELECT 1 FROM inventory_ledger g WHERE g.return_case_id=cid AND g.movement_type='RETURN'
    AND g.return_order_line_id=l.id AND g.warehouse_id=l.warehouse_id AND g.sku_id=l.sku_id
    AND g.unit_version_id=l.unit_version_id AND g.operation_quantity=a.salable_quantity
    AND g.ratio=l.ratio AND g.delta_base_units=a.salable_quantity::bigint*l.ratio AND g.actor_id=a.actor_id)
  THEN RAISE EXCEPTION 'salable return requires exact warehouse ledger'; END IF;
 ELSIF EXISTS(SELECT 1 FROM inventory_ledger WHERE return_case_id=cid)
 THEN RAISE EXCEPTION 'unaccepted stock cannot be restored'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER return_acceptance_effects AFTER INSERT ON aftersale_return_acceptance
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_return_effects();
CREATE CONSTRAINT TRIGGER return_disposition_effects AFTER INSERT ON inventory_return_disposition
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_return_effects();
CREATE CONSTRAINT TRIGGER return_inventory_effects AFTER INSERT ON inventory_ledger
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_return_effects();
CREATE CONSTRAINT TRIGGER return_case_effects AFTER INSERT OR UPDATE ON aftersale_case
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_return_effects();
"""
REVERSE=r"""
DROP TRIGGER return_case_effects ON aftersale_case;
DROP TRIGGER return_inventory_effects ON inventory_ledger;
DROP TRIGGER return_disposition_effects ON inventory_return_disposition;
DROP TRIGGER return_acceptance_effects ON aftersale_return_acceptance;
DROP FUNCTION check_return_effects();
DROP TRIGGER return_shipment_request ON aftersale_return_shipment;
DROP TRIGGER return_acceptance_request ON aftersale_return_acceptance;
DROP FUNCTION return_request_guard();
DROP TRIGGER return_shipment_immutable ON aftersale_return_shipment;
DROP TRIGGER return_acceptance_immutable ON aftersale_return_acceptance;
DROP TRIGGER return_disposition_immutable ON inventory_return_disposition;
"""

class Migration(migrations.Migration):
    dependencies=[('aftersales','0004_returnacceptance_returnshipment'),('inventory','0010_returndisposition_and_more'),('payments','0015_offlinerefundreconciliation_offline_refund_outcome_shape')]
    operations=[migrations.RunSQL(CASE+AGG+FUNDS+EFFECT+SQL,REVERSE+''.join(function(s,n).replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1) for s,n in [(OLD_CASE,'aftersale_case_guard()'),(OLD_CASE,'aftersale_check_aggregate()'),(OLD_FUNDS,'check_refund_final_state()'),(OLD_EFFECT,'check_refund_domain_effects()')]))]
