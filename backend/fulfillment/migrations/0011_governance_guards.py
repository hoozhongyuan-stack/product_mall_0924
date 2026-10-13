"""Enforce actor, scope, handoff quantity and refund provenance at the database."""
import importlib
from django.db import migrations
old = importlib.import_module('fulfillment.migrations.0007_store_handoff_guards')
returns = importlib.import_module('aftersales.migrations.0005_return_guards')
pool = importlib.import_module('inventory.migrations.0012_pool_evidence_guards')


def backfill(apps, schema_editor):
    Shipment = apps.get_model('fulfillment', 'Shipment')
    ShipmentLine = apps.get_model('fulfillment', 'ShipmentLine')
    Local = apps.get_model('fulfillment', 'StoreDelivery')
    Event = apps.get_model('fulfillment', 'StoreDeliveryEvent')
    Handoff = apps.get_model('fulfillment', 'LocalHandoffLine')
    Line = apps.get_model('orders', 'OrderLine')
    Case = apps.get_model('aftersales', 'AfterSaleCase')
    for shipment in Shipment.objects.all().iterator():
        for line in Line.objects.filter(order_id=shipment.order_id, fulfillment_kind='SHIP'):
            refunded = sum(Case.objects.filter(order_line_id=line.id, kind='REFUND_ONLY', status='COMPLETED').values_list('quantity', flat=True))
            if line.quantity > refunded:
                ShipmentLine.objects.get_or_create(shipment_id=shipment.id, order_line_id=line.id, defaults={'quantity': line.quantity-refunded})
    for delivery in Local.objects.filter(status__in=['IN_TRANSIT', 'COMPLETED']).iterator():
        phases = ['DISPATCH', 'COMPLETE'] if delivery.mode == 'DELIVERY' and delivery.status == 'COMPLETED' else ['COMPLETE'] if delivery.mode == 'PICKUP' else ['DISPATCH']
        for phase in phases:
            event = Event.objects.filter(order_id=delivery.order_id, action=phase).order_by('-occurred_at').first()
            if event:
                for line in Line.objects.filter(order_id=delivery.order_id, fulfillment_kind='SHIP'):
                    refunded = sum(Case.objects.filter(order_line_id=line.id, kind='REFUND_ONLY', status='COMPLETED').values_list('quantity', flat=True))
                    if line.quantity > refunded:
                        Handoff.objects.get_or_create(event_id=event.id, order_line_id=line.id, defaults={'phase': phase, 'quantity': line.quantity-refunded})


SQL = r"""
CREATE FUNCTION fg_dispatched(line_id uuid) RETURNS bigint LANGUAGE SQL STABLE AS $$
 SELECT COALESCE((SELECT SUM(quantity) FROM fulfillment_shipment_line WHERE order_line_id=line_id),0) +
 GREATEST(COALESCE((SELECT SUM(quantity) FROM fulfillment_local_handoff_line WHERE order_line_id=line_id AND phase='DISPATCH'),0),
          COALESCE((SELECT SUM(quantity) FROM fulfillment_local_handoff_line WHERE order_line_id=line_id AND phase='COMPLETE'),0))
$$;
CREATE FUNCTION fg_unshipped_refunded(line_id uuid) RETURNS bigint LANGUAGE SQL STABLE AS $$
 SELECT COALESCE(SUM(quantity),0) FROM aftersale_case WHERE order_line_id=line_id AND kind='REFUND_ONLY' AND status='COMPLETED'
$$;
CREATE TRIGGER shipment_line_immutable BEFORE UPDATE OR DELETE ON fulfillment_shipment_line FOR EACH ROW EXECUTE FUNCTION fulfillment_reject_mutation();
CREATE TRIGGER local_handoff_immutable BEFORE UPDATE OR DELETE ON fulfillment_local_handoff_line FOR EACH ROW EXECUTE FUNCTION fulfillment_reject_mutation();
CREATE FUNCTION fg_quantity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE l customer_order_line%ROWTYPE; o customer_order%ROWTYPE; e fulfillment_store_delivery_event%ROWTYPE; sh fulfillment_shipment%ROWTYPE; held bigint; total bigint; completed bigint;
BEGIN
 SELECT * INTO l FROM customer_order_line WHERE id=NEW.order_line_id;
 SELECT * INTO o FROM customer_order WHERE id=l.order_id FOR UPDATE;
 IF o.status<>'PAID' OR l.fulfillment_kind<>'SHIP' THEN RAISE EXCEPTION 'handoff requires paid physical order item'; END IF;
 SELECT COALESCE(SUM(quantity),0) INTO held FROM aftersale_case WHERE order_line_id=l.id AND kind='REFUND_ONLY' AND status IN ('PENDING_REVIEW','WAITING_RETURN','WAITING_REFUND');
 IF TG_TABLE_NAME='fulfillment_shipment_line' THEN
  SELECT * INTO sh FROM fulfillment_shipment WHERE id=NEW.shipment_id;
  IF sh.order_id IS DISTINCT FROM o.id OR sh.warehouse_id IS DISTINCT FROM l.warehouse_id OR sh.shipped_by_id IS NULL OR o.store_id IS NOT NULL OR o.delivery_mode NOT IN ('','EXPRESS')
   OR NOT EXISTS(SELECT 1 FROM warehouse WHERE id=l.warehouse_id AND is_default=true)
  THEN RAISE EXCEPTION 'express must be fulfilled by platform warehouse'; END IF;
  total:=fg_dispatched(l.id)+NEW.quantity;
 ELSIF TG_TABLE_NAME='fulfillment_local_handoff_line' THEN
  SELECT * INTO e FROM fulfillment_store_delivery_event WHERE id=NEW.event_id;
  IF e.order_id IS DISTINCT FROM o.id OR e.actor_id IS NULL OR e.admin_actor_id IS NOT NULL OR e.action IS DISTINCT FROM NEW.phase OR o.store_id IS NULL
   OR NOT EXISTS(SELECT 1 FROM stores_store WHERE id=o.store_id AND warehouse_id=l.warehouse_id)
  THEN RAISE EXCEPTION 'local handoff requires matching warehouse staff event'; END IF;
  IF NEW.phase='DISPATCH' THEN total:=fg_dispatched(l.id)+NEW.quantity;
  ELSE
   SELECT COALESCE(SUM(quantity),0)+NEW.quantity INTO completed FROM fulfillment_local_handoff_line WHERE order_line_id=l.id AND phase='COMPLETE';
   IF o.delivery_mode='DELIVERY' AND completed>fg_dispatched(l.id) THEN RAISE EXCEPTION 'delivery completion exceeds dispatched quantity'; END IF;
   total:=CASE WHEN o.delivery_mode='PICKUP' THEN completed ELSE fg_dispatched(l.id) END;
  END IF;
 END IF;
 IF total+held+fg_unshipped_refunded(l.id)>l.quantity THEN RAISE EXCEPTION 'handoff exceeds unfrozen purchased capacity'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER shipment_line_guard BEFORE INSERT ON fulfillment_shipment_line FOR EACH ROW EXECUTE FUNCTION fg_quantity_guard();
CREATE TRIGGER local_handoff_guard BEFORE INSERT ON fulfillment_local_handoff_line FOR EACH ROW EXECUTE FUNCTION fg_quantity_guard();
CREATE OR REPLACE FUNCTION store_delivery_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o customer_order%ROWTYPE;
BEGIN
 SELECT * INTO o FROM customer_order WHERE id=NEW.order_id FOR UPDATE;
 IF o.status<>'PAID' OR o.store_id IS NULL OR NEW.mode IS DISTINCT FROM o.delivery_mode THEN RAISE EXCEPTION 'local fulfillment requires paid matching store'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (NEW.order_id,NEW.mode) IS DISTINCT FROM (OLD.order_id,OLD.mode) OR NOT (
    (OLD.status='READY' AND NEW.status='COMPLETED' AND NEW.mode='PICKUP') OR
    (OLD.status='READY' AND NEW.status='IN_TRANSIT' AND NEW.mode='DELIVERY') OR
    (OLD.status='IN_TRANSIT' AND NEW.status IN ('COMPLETED','EXCEPTION') AND NEW.mode='DELIVERY') OR
    (OLD.status='EXCEPTION' AND NEW.status='IN_TRANSIT' AND NEW.mode='DELIVERY') OR
    (OLD.status='COMPLETED' AND NEW.status='READY' AND EXISTS(SELECT 1 FROM customer_order_line l WHERE l.order_id=o.id AND l.fulfillment_kind='SHIP' AND l.quantity>fg_dispatched(l.id)+fg_unshipped_refunded(l.id))))
  THEN RAISE EXCEPTION 'invalid physical handoff transition'; END IF;
  IF OLD.status<>'COMPLETED' AND (NEW.nonce,NEW.prepared_at) IS DISTINCT FROM (OLD.nonce,OLD.prepared_at) THEN RAISE EXCEPTION 'batch identity is immutable'; END IF;
 ELSIF NEW.status<>'READY' THEN RAISE EXCEPTION 'store delivery starts prepared'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION store_delivery_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE action_code varchar;
BEGIN
 action_code:=CASE NEW.status WHEN 'READY' THEN 'PREPARE' WHEN 'EXCEPTION' THEN 'FAIL_DELIVERY' WHEN 'IN_TRANSIT' THEN CASE WHEN TG_OP='UPDATE' AND OLD.status='EXCEPTION' THEN 'RETRY_DELIVERY' ELSE 'DISPATCH' END ELSE 'COMPLETE' END;
 IF NOT EXISTS(SELECT 1 FROM fulfillment_store_delivery_event e WHERE e.order_id=NEW.order_id AND e.action=action_code AND e.occurred_at>=transaction_timestamp()) THEN RAISE EXCEPTION 'handoff transition requires current event'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION fg_redeem_scope_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE l customer_order_line%ROWTYPE; o customer_order%ROWTYPE; scope varchar;
BEGIN
 SELECT line.* INTO l FROM customer_order_line line JOIN fulfillment_redeem_voucher v ON v.order_line_id=line.id WHERE v.id=NEW.voucher_id;
 SELECT * INTO o FROM customer_order WHERE id=l.order_id FOR UPDATE;
 IF o.status<>'PAID' OR l.fulfillment_kind<>'REDEEM' THEN RAISE EXCEPTION 'redemption requires paid rights'; END IF;
 IF NEW.kind='REVERSE' THEN RETURN NEW; END IF;
 scope:=COALESCE(l.redeem_scope_snapshot->>'scope','PLATFORM');
 IF (NEW.actor_id IS NOT NULL AND scope NOT IN ('PLATFORM','BOTH')) OR
    (NEW.store_staff_actor_id IS NOT NULL AND (scope NOT IN ('STORE','BOTH') OR NOT EXISTS(
      SELECT 1 FROM jsonb_array_elements(COALESCE(l.redeem_scope_snapshot->'stores','[]'::jsonb)) row WHERE row->>'storeId'=NEW.store_id::text)))
 THEN RAISE EXCEPTION 'redemption actor outside transaction snapshot scope'; END IF;
 IF NEW.store_staff_actor_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stores_storestaff st JOIN customer_member m ON m.id=st.member_id
 JOIN stores_store store ON store.id=st.store_id JOIN warehouse w ON w.id=store.warehouse_id WHERE st.member_id=NEW.store_staff_actor_id AND st.store_id=NEW.store_id AND st.enabled=true AND m.enabled=true AND store.enabled=true AND w.enabled=true AND st.permissions ? 'orders')
 THEN RAISE EXCEPTION 'redemption requires authorized active staff'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER fg_redeem_scope BEFORE INSERT ON fulfillment_redeem_event FOR EACH ROW EXECUTE FUNCTION fg_redeem_scope_guard();
"""
SQL += r"""
CREATE FUNCTION fg_local_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o customer_order%ROWTYPE;
BEGIN
 SELECT * INTO o FROM customer_order WHERE id=NEW.order_id FOR UPDATE;
 IF o.status<>'PAID' OR o.store_id IS NULL OR o.delivery_mode NOT IN ('PICKUP','DELIVERY') OR NEW.actor_id IS NULL OR NEW.admin_actor_id IS NOT NULL OR NOT EXISTS(
 SELECT 1 FROM stores_storestaff st JOIN customer_member m ON m.id=st.member_id WHERE st.store_id=o.store_id AND st.member_id=NEW.actor_id AND st.enabled AND m.enabled AND st.permissions ? 'orders')
 THEN RAISE EXCEPTION 'local handoff event requires active warehouse staff'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER fg_local_event_actor BEFORE INSERT ON fulfillment_store_delivery_event FOR EACH ROW EXECUTE FUNCTION fg_local_event_guard();
CREATE FUNCTION fg_quantity_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='fulfillment_shipment' THEN
  IF NOT EXISTS(SELECT 1 FROM fulfillment_shipment_line WHERE shipment_id=NEW.id) THEN RAISE EXCEPTION 'shipment requires item quantity evidence'; END IF;
 ELSE
  IF NEW.action IN ('DISPATCH','COMPLETE') AND NOT EXISTS(SELECT 1 FROM fulfillment_local_handoff_line WHERE event_id=NEW.id) THEN RAISE EXCEPTION 'handoff requires item quantity evidence'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER fg_shipment_items AFTER INSERT ON fulfillment_shipment DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fg_quantity_evidence();
CREATE CONSTRAINT TRIGGER fg_handoff_items AFTER INSERT ON fulfillment_store_delivery_event DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fg_quantity_evidence();
CREATE FUNCTION fg_after_sale_quantity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE l customer_order_line%ROWTYPE; previous bigint;
BEGIN
 SELECT * INTO l FROM customer_order_line WHERE id=NEW.order_line_id;
 PERFORM id FROM customer_order WHERE id=l.order_id FOR UPDATE;
 IF l.fulfillment_kind='SHIP' THEN
  IF NEW.kind='REFUND_ONLY' AND NEW.quantity>l.quantity-fg_dispatched(l.id)-fg_unshipped_refunded(l.id) THEN RAISE EXCEPTION 'unshipped refund cannot consume handed quantities'; END IF;
  SELECT COALESCE(SUM(COALESCE(a.refund_quantity,c.quantity)),0) INTO previous FROM aftersale_case c LEFT JOIN aftersale_return_acceptance a ON a.case_id=c.id WHERE c.order_line_id=l.id AND c.kind='RETURN_REFUND' AND c.status='COMPLETED';
  IF NEW.kind='RETURN_REFUND' AND NEW.quantity>fg_dispatched(l.id)-previous THEN RAISE EXCEPTION 'return cannot consume unshipped quantities'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER fg_after_sale_quantity BEFORE INSERT ON aftersale_case FOR EACH ROW EXECUTE FUNCTION fg_after_sale_quantity_guard();
"""
RETURN = pool.RETURN_NEW.replace('NOT EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id)', 'fg_dispatched(l.id)=0').replace('received>l.quantity', 'received>fg_dispatched(l.id)')
AMOUNT = old.amount_sql
EFFECT = old.effect_sql.replace("EXISTS(SELECT 1 FROM fulfillment_shipment WHERE order_id=l.order_id) OR EXISTS(SELECT 1 FROM fulfillment_store_delivery WHERE order_id=l.order_id AND status IN ('IN_TRANSIT','COMPLETED')) OR", 'fg_dispatched(l.id)+fg_unshipped_refunded(l.id)>l.quantity OR')


class Migration(migrations.Migration):
    dependencies = [('fulfillment', '0010_governance_models'), ('orders', '0018_fulfillment_governance_snapshot'), ('aftersales', '0011_case_business_number')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop), migrations.RunSQL(SQL + RETURN + AMOUNT + EFFECT)]
