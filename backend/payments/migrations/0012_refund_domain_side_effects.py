from django.db import migrations

SQL = r"""
CREATE FUNCTION check_refund_domain_effects() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cid uuid; c aftersale_case%ROWTYPE; l customer_order_line%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME = 'aftersale_case' THEN cid := NEW.id;
 ELSIF TG_TABLE_NAME = 'fulfillment_voucher_refund_event' THEN cid := NEW.case_id;
 ELSE
   IF NEW.movement_type <> 'REFUND' THEN RETURN NULL; END IF;
   cid := NEW.refund_case_id;
 END IF;
 SELECT * INTO c FROM aftersale_case WHERE id=cid;
 IF NOT FOUND THEN RAISE EXCEPTION 'refund effect requires real aftersale'; END IF;
 SELECT * INTO l FROM customer_order_line WHERE id=c.order_line_id;
 IF TG_TABLE_NAME <> 'aftersale_case' AND c.status <> 'COMPLETED'
 THEN RAISE EXCEPTION 'refund effect requires completed aftersale'; END IF;
 IF c.status <> 'COMPLETED' THEN RETURN NULL; END IF;
 IF l.fulfillment_kind='SHIP' AND c.kind='REFUND_ONLY' THEN
   IF EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id) OR
      NOT EXISTS(SELECT 1 FROM inventory_ledger g WHERE g.refund_case_id=cid
        AND g.movement_type='REFUND' AND g.refund_order_line_id=l.id
        AND g.warehouse_id=l.warehouse_id AND g.sku_id=l.sku_id
        AND g.unit_version_id=l.unit_version_id AND g.operation_quantity=c.quantity
        AND g.ratio=l.ratio AND g.delta_base_units=c.quantity::bigint*l.ratio)
   THEN RAISE EXCEPTION 'unshipped refund requires matching inventory restitution'; END IF;
 ELSE
   IF EXISTS(SELECT 1 FROM inventory_ledger WHERE refund_case_id=cid)
   THEN RAISE EXCEPTION 'only unshipped cash refund may use automatic restitution'; END IF;
 END IF;
 IF l.fulfillment_kind='REDEEM' AND c.unredeemed_quantity>0 THEN
   IF NOT EXISTS(SELECT 1 FROM fulfillment_voucher_refund_event e
     JOIN fulfillment_redeem_voucher v ON v.id=e.voucher_id
     WHERE e.case_id=cid AND v.order_line_id=l.id AND e.quantity=c.unredeemed_quantity)
   THEN RAISE EXCEPTION 'unused refund requires matching voucher void'; END IF;
 ELSE
   IF EXISTS(SELECT 1 FROM fulfillment_voucher_refund_event WHERE case_id=cid)
   THEN RAISE EXCEPTION 'used refund must not void unredeemed capacity'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER refund_case_effects AFTER INSERT OR UPDATE ON aftersale_case
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_refund_domain_effects();
CREATE CONSTRAINT TRIGGER refund_inventory_effects AFTER INSERT OR UPDATE ON inventory_ledger
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_refund_domain_effects();
CREATE CONSTRAINT TRIGGER refund_voucher_effects AFTER INSERT OR UPDATE ON fulfillment_voucher_refund_event
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_refund_domain_effects();
"""

REVERSE = r"""
DROP TRIGGER refund_voucher_effects ON fulfillment_voucher_refund_event;
DROP TRIGGER refund_inventory_effects ON inventory_ledger;
DROP TRIGGER refund_case_effects ON aftersale_case;
DROP FUNCTION check_refund_domain_effects();
"""


class Migration(migrations.Migration):
    dependencies = [("payments", "0011_refund_evidence_guards")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
