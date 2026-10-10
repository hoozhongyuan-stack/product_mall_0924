"""Preserve refund/return evidence checks for physical store handoff."""
import importlib
from django.db import migrations
old = importlib.import_module('aftersales.migrations.0005_return_guards')
settlement = importlib.import_module('aftersales.migrations.0007_d3_settlement_guards')
pool = importlib.import_module('inventory.migrations.0012_pool_evidence_guards')
# Compose with the latest anchor-aware stock provenance guard, not its pre-pool predecessor.
return_sql = pool.RETURN_NEW
return_sql = return_sql.replace('NOT EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id)', "NOT EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id) AND NOT EXISTS(SELECT 1 FROM fulfillment_store_delivery WHERE order_id=l.order_id AND status IN ('IN_TRANSIT','COMPLETED'))")
# Parenthesize the joint absence before the original OR stock provenance clause.
return_sql = return_sql.replace("IF NOT EXISTS(SELECT 1 FROM fulfillment_shipment", "IF (NOT EXISTS(SELECT 1 FROM fulfillment_shipment").replace("status IN ('IN_TRANSIT','COMPLETED')) OR", "status IN ('IN_TRANSIT','COMPLETED'))) OR")
amount_sql = old.function(settlement.SQL, 'd3_refund_amount_guard()').replace('CREATE FUNCTION', 'CREATE OR REPLACE FUNCTION', 1)
amount_sql = amount_sql.replace('EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=o.id) OR', "EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=o.id) OR EXISTS(SELECT 1 FROM fulfillment_store_delivery WHERE order_id=o.id AND status IN ('IN_TRANSIT','COMPLETED')) OR")
effect_sql = old.EFFECT.replace('EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id) OR', "EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id) OR EXISTS(SELECT 1 FROM fulfillment_store_delivery WHERE order_id=l.order_id AND status IN ('IN_TRANSIT','COMPLETED')) OR")
GUARDS = r"""
CREATE FUNCTION store_delivery_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o customer_order%ROWTYPE;
BEGIN
 SELECT * INTO o FROM customer_order WHERE id=NEW.order_id FOR UPDATE;
 IF o.status <> 'PAID' OR o.store_id IS NULL OR NEW.mode IS DISTINCT FROM o.delivery_mode
 THEN RAISE EXCEPTION 'store delivery requires paid matching store order'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.order_id,NEW.mode,NEW.nonce,NEW.prepared_at) IS DISTINCT FROM (OLD.order_id,OLD.mode,OLD.nonce,OLD.prepared_at) OR
   NOT ((OLD.status='READY' AND NEW.status='COMPLETED' AND NEW.mode='PICKUP') OR
        (OLD.status='READY' AND NEW.status='IN_TRANSIT' AND NEW.mode='DELIVERY') OR
        (OLD.status='IN_TRANSIT' AND NEW.status='COMPLETED' AND NEW.mode='DELIVERY'))
  THEN RAISE EXCEPTION 'invalid physical handoff transition'; END IF;
 ELSIF NEW.status<>'READY' THEN RAISE EXCEPTION 'store delivery starts prepared'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER store_delivery_insert_update BEFORE INSERT OR UPDATE ON fulfillment_store_delivery
FOR EACH ROW EXECUTE FUNCTION store_delivery_guard();
CREATE TRIGGER store_delivery_event_immutable BEFORE UPDATE OR DELETE ON fulfillment_store_delivery_event
FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();
CREATE FUNCTION store_delivery_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM fulfillment_store_delivery_event e WHERE e.order_id=NEW.order_id AND
 e.action=CASE NEW.status WHEN 'READY' THEN 'PREPARE' WHEN 'IN_TRANSIT' THEN 'DISPATCH' ELSE 'COMPLETE' END)
 THEN RAISE EXCEPTION 'store handoff requires event'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER store_delivery_requires_event AFTER INSERT OR UPDATE ON fulfillment_store_delivery
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION store_delivery_evidence();
"""
REVERSE = r"""
DROP TRIGGER store_delivery_requires_event ON fulfillment_store_delivery;
DROP FUNCTION store_delivery_evidence();
DROP TRIGGER store_delivery_event_immutable ON fulfillment_store_delivery_event;
DROP TRIGGER store_delivery_insert_update ON fulfillment_store_delivery;
DROP FUNCTION store_delivery_guard();
"""
class Migration(migrations.Migration):
    dependencies = [('fulfillment', '0006_storedelivery_storedeliveryevent'), ('aftersales', '0007_d3_settlement_guards'), ('orders', '0015_store_context'), ('inventory', '0012_pool_evidence_guards')]
    operations = [migrations.RunSQL(return_sql + amount_sql + effect_sql + GUARDS, REVERSE + old.EFFECT + pool.RETURN_NEW + old.function(settlement.SQL,'d3_refund_amount_guard()').replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1))]
