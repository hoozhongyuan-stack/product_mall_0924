from django.db import migrations
from importlib import import_module

_previous=import_module('orders.migrations.0012_points_consumption_proofs').Migration.operations[0].sql
PREVIOUS_RESERVATION_GUARD=_previous[_previous.index('CREATE FUNCTION exchange_points_reservation_guard()'): _previous.index('CREATE TRIGGER exchange_points_reservation_guard')].replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)
PREVIOUS_GUARD=_previous[_previous.index('CREATE OR REPLACE FUNCTION order_points_total_guard()'): _previous.index('CREATE CONSTRAINT TRIGGER reservation_exchange_total_guard')]

FORWARD = '''
CREATE OR REPLACE FUNCTION order_points_total_guard() RETURNS trigger AS $$
DECLARE identity uuid; kind text; state text; total bigint; line_total bigint; buyer uuid;
BEGIN
 IF TG_TABLE_NAME='customer_order' THEN identity=NEW.id; ELSE identity=NEW.order_id; END IF;
 SELECT order_kind,status,points_to_use,member_id INTO kind,state,total,buyer FROM customer_order WHERE id=identity;
 IF kind='POINTS' THEN
  SELECT COALESCE(SUM(points_total),0) INTO line_total FROM customer_order_line WHERE order_id=identity;
  IF total!=line_total THEN RAISE EXCEPTION 'exchange points total must match line snapshots'; END IF;
  IF EXISTS(SELECT 1 FROM benefit_points_reservation r JOIN benefit_points_grant g ON g.id=r.grant_id
     WHERE r.order_id=identity AND (r.member_id!=buyer OR g.member_id!=buyer)) THEN
   RAISE EXCEPTION 'exchange points proof must belong to order member'; END IF;
  IF state='PAID' AND (
   total!=COALESCE((SELECT SUM(amount) FROM benefit_points_reservation WHERE order_id=identity AND status='CONSUMED'),0)
   OR EXISTS(SELECT 1 FROM benefit_points_reservation r WHERE r.order_id=identity AND NOT EXISTS(
      SELECT 1 FROM benefit_points_event e WHERE e.order_id=identity AND e.grant_id=r.grant_id AND e.member_id=buyer AND e.kind='CONSUME' AND e.amount=r.amount))
   OR total!=COALESCE((SELECT SUM(e.amount) FROM benefit_points_event e WHERE e.order_id=identity AND e.kind='CONSUME' AND e.member_id=buyer),0)
   OR EXISTS(SELECT 1 FROM customer_order_line l WHERE l.order_id=identity AND NOT EXISTS(
      SELECT 1 FROM inventory_reservation r JOIN inventory_balance b ON b.id=r.balance_id
      WHERE r.order_line_id=l.id AND r.status='CONSUMED' AND r.base_quantity=l.base_quantity AND b.sku_id=l.sku_id AND b.warehouse_id=l.warehouse_id))
   OR EXISTS(SELECT 1 FROM customer_order_line l WHERE l.order_id=identity AND NOT EXISTS(
      SELECT 1 FROM inventory_ledger e WHERE e.order_line_id=l.id AND e.movement_type='SALE' AND e.delta_base_units=-l.base_quantity
         AND e.sku_id=l.sku_id AND e.warehouse_id=l.warehouse_id AND e.unit_version_id=l.unit_version_id
         AND e.operation_quantity=l.quantity AND e.ratio=l.ratio))
   OR (SELECT COUNT(*) FROM inventory_ledger e JOIN customer_order_line l ON l.id=e.order_line_id WHERE l.order_id=identity AND e.movement_type='SALE')
      !=(SELECT COUNT(*) FROM customer_order_line WHERE order_id=identity)
  ) THEN RAISE EXCEPTION 'paid exchange order requires exact immutable consumption events'; END IF;
 END IF;
 RETURN NULL;
END; $$ LANGUAGE plpgsql;
CREATE OR REPLACE FUNCTION exchange_points_reservation_guard() RETURNS trigger AS $$
DECLARE new_kind text; old_kind text;
BEGIN
 IF TG_OP!='DELETE' THEN SELECT order_kind INTO new_kind FROM customer_order WHERE id=NEW.order_id; END IF;
 SELECT order_kind INTO old_kind FROM customer_order WHERE id=OLD.order_id;
 IF (old_kind='POINTS' OR new_kind='POINTS') AND TG_OP='DELETE' THEN RAISE EXCEPTION 'exchange points proof cannot be deleted'; END IF;
 IF (old_kind='POINTS' OR new_kind='POINTS') AND TG_OP='UPDATE' AND
   (ROW(NEW.member_id,NEW.grant_id,NEW.order_id,NEW.amount) IS DISTINCT FROM ROW(OLD.member_id,OLD.grant_id,OLD.order_id,OLD.amount)
    OR (OLD.status='CONSUMED' AND NEW.status!='CONSUMED')) THEN RAISE EXCEPTION 'exchange points proof cannot be moved or changed'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE FUNCTION exchange_inventory_consumption_guard() RETURNS trigger AS $$
DECLARE kind text;
BEGIN
 SELECT o.order_kind INTO kind FROM customer_order o JOIN customer_order_line l ON l.order_id=o.id WHERE l.id=OLD.order_line_id;
 IF kind='POINTS' AND OLD.status='CONSUMED' THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'exchange stock proof cannot be deleted'; END IF;
  IF ROW(NEW.order_line_id,NEW.balance_id,NEW.base_quantity,NEW.status,NEW.consumed_at) IS DISTINCT FROM
     ROW(OLD.order_line_id,OLD.balance_id,OLD.base_quantity,OLD.status,OLD.consumed_at) THEN
    RAISE EXCEPTION 'exchange stock proof is immutable'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER exchange_inventory_consumption_guard BEFORE UPDATE OR DELETE ON inventory_reservation FOR EACH ROW EXECUTE FUNCTION exchange_inventory_consumption_guard();
'''

class Migration(migrations.Migration):
    dependencies=[('orders','0012_points_consumption_proofs')]
    operations=[migrations.RunSQL(FORWARD,'''DROP TRIGGER exchange_inventory_consumption_guard ON inventory_reservation;
      DROP FUNCTION exchange_inventory_consumption_guard();'''+PREVIOUS_GUARD+PREVIOUS_RESERVATION_GUARD)]
