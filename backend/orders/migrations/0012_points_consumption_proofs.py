from django.db import migrations
from importlib import import_module

_previous=import_module("orders.migrations.0011_points_order_guards").Migration.operations[0].sql
PREVIOUS_GUARD=_previous[_previous.index("CREATE FUNCTION order_points_total_guard()"): _previous.index("CREATE CONSTRAINT TRIGGER order_points_total_guard")].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION",1)

class Migration(migrations.Migration):
    dependencies=[('orders','0011_points_order_guards'),('benefits','0014_remove_couponissuance_coupon_actor_issue_key_unique_and_more')]
    operations=[migrations.RunSQL('''
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
          IF state='PAID' AND (total!=COALESCE((SELECT SUM(amount) FROM benefit_points_reservation WHERE order_id=identity AND status='CONSUMED'),0)
              OR EXISTS(SELECT 1 FROM inventory_reservation r JOIN customer_order_line l ON l.id=r.order_line_id
                         WHERE l.order_id=identity AND (r.status!='CONSUMED' OR r.base_quantity!=l.base_quantity))
              OR (SELECT COUNT(*) FROM inventory_reservation r JOIN customer_order_line l ON l.id=r.order_line_id WHERE l.order_id=identity)
                 !=(SELECT COUNT(*) FROM customer_order_line WHERE order_id=identity)) THEN
            RAISE EXCEPTION 'paid exchange order requires exact points and inventory consumption proof'; END IF;
        END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql;
      CREATE CONSTRAINT TRIGGER reservation_exchange_total_guard AFTER INSERT OR UPDATE ON benefit_points_reservation DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION order_points_total_guard();
      CREATE FUNCTION exchange_points_reservation_guard() RETURNS trigger AS $$
      DECLARE kind text; identity uuid;
      BEGIN
        IF TG_OP='DELETE' THEN identity=OLD.order_id; ELSE identity=NEW.order_id; END IF;
        SELECT order_kind INTO kind FROM customer_order WHERE id=identity;
        IF kind='POINTS' AND TG_OP='DELETE' THEN RAISE EXCEPTION 'exchange consumption proof cannot be deleted'; END IF;
        IF kind='POINTS' AND TG_OP='UPDATE' AND (ROW(NEW.member_id,NEW.grant_id,NEW.order_id,NEW.amount) IS DISTINCT FROM ROW(OLD.member_id,OLD.grant_id,OLD.order_id,OLD.amount)
          OR (OLD.status='CONSUMED' AND NEW.status!='CONSUMED')) THEN
          RAISE EXCEPTION 'exchange consumed points proof is immutable'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
      CREATE TRIGGER exchange_points_reservation_guard BEFORE UPDATE OR DELETE ON benefit_points_reservation FOR EACH ROW EXECUTE FUNCTION exchange_points_reservation_guard();
    ''','''
      DROP TRIGGER exchange_points_reservation_guard ON benefit_points_reservation;
      DROP FUNCTION exchange_points_reservation_guard();
      DROP TRIGGER reservation_exchange_total_guard ON benefit_points_reservation;
    '''+PREVIOUS_GUARD)]
