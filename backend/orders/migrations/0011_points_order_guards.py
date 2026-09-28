from django.db import migrations

CASH = """COALESCE((benefit_policy_snapshot='{}'::jsonb AND points_discount_fen=points_to_use) OR
 (benefit_policy_snapshot ? 'deductPoints' AND benefit_policy_snapshot ? 'deductFen'
  AND (benefit_policy_snapshot->>'deductPoints')::bigint > 0
  AND (benefit_policy_snapshot->>'deductFen')::bigint > 0
  AND points_discount_fen=points_to_use::bigint * (benefit_policy_snapshot->>'deductFen')::bigint
      / (benefit_policy_snapshot->>'deductPoints')::bigint),FALSE)"""

class Migration(migrations.Migration):
    dependencies=[('orders','0010_order_order_kind_orderline_points_total_and_more')]
    operations=[migrations.RunSQL('''
      ALTER TABLE customer_order DROP CONSTRAINT order_policy_conversion_consistent;
      ALTER TABLE customer_order ADD CONSTRAINT order_policy_conversion_consistent CHECK
      ((order_kind='CASH' AND '''+CASH+''') OR
       (order_kind='POINTS' AND payment_method='POINTS' AND points_to_use>0 AND goods_total_fen=0 AND shipping_fee_fen=0
         AND coupon_id IS NULL AND coupon_discount_fen=0 AND points_discount_fen=0 AND payable_fen=0));
      CREATE FUNCTION order_points_kind_immutable() RETURNS trigger AS $$
      BEGIN
        IF NEW.order_kind IS DISTINCT FROM OLD.order_kind THEN RAISE EXCEPTION 'order kind is immutable'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
      CREATE TRIGGER order_points_kind_immutable BEFORE UPDATE ON customer_order FOR EACH ROW EXECUTE FUNCTION order_points_kind_immutable();
      CREATE FUNCTION order_points_line_guard() RETURNS trigger AS $$
      DECLARE kind text;
      BEGIN
        SELECT order_kind INTO kind FROM customer_order WHERE id=NEW.order_id;
        IF (kind='CASH' AND (NEW.points_unit_price!=0 OR NEW.points_total!=0)) OR
           (kind='POINTS' AND (NEW.points_unit_price<=0 OR NEW.points_total!=NEW.points_unit_price*NEW.quantity)) THEN
          RAISE EXCEPTION 'order line points must match order kind'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
      CREATE TRIGGER order_points_line_guard BEFORE INSERT ON customer_order_line FOR EACH ROW EXECUTE FUNCTION order_points_line_guard();
      CREATE FUNCTION order_points_total_guard() RETURNS trigger AS $$
      DECLARE identity uuid; kind text; state text; total bigint; line_total bigint;
      BEGIN
        IF TG_TABLE_NAME='customer_order' THEN identity=NEW.id; ELSE identity=NEW.order_id; END IF;
        SELECT order_kind,status,points_to_use INTO kind,state,total FROM customer_order WHERE id=identity;
        IF kind='POINTS' THEN
          SELECT COALESCE(SUM(points_total),0) INTO line_total FROM customer_order_line WHERE order_id=identity;
          IF total!=line_total THEN RAISE EXCEPTION 'exchange points total must match line snapshots'; END IF;
          IF state='PAID' AND total!=COALESCE((SELECT SUM(amount) FROM benefit_points_reservation WHERE order_id=identity AND status='CONSUMED'),0) THEN
            RAISE EXCEPTION 'paid exchange order requires consumed points proof'; END IF;
        END IF;
        RETURN NULL;
      END; $$ LANGUAGE plpgsql;
      CREATE CONSTRAINT TRIGGER order_points_total_guard AFTER INSERT OR UPDATE ON customer_order DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION order_points_total_guard();
      CREATE CONSTRAINT TRIGGER order_line_points_total_guard AFTER INSERT ON customer_order_line DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION order_points_total_guard();
    ''','''
      DROP TRIGGER order_line_points_total_guard ON customer_order_line;
      DROP TRIGGER order_points_total_guard ON customer_order;
      DROP FUNCTION order_points_total_guard();
      DROP TRIGGER order_points_line_guard ON customer_order_line;
      DROP FUNCTION order_points_line_guard();
      DROP TRIGGER order_points_kind_immutable ON customer_order;
      DROP FUNCTION order_points_kind_immutable();
      ALTER TABLE customer_order DROP CONSTRAINT order_policy_conversion_consistent;
      ALTER TABLE customer_order ADD CONSTRAINT order_policy_conversion_consistent CHECK ('''+CASH+''');
    ''')]
