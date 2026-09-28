from django.db import migrations

class Migration(migrations.Migration):
    dependencies=[('payments','0018_wechat_refund_notice_guards'),('orders','0012_points_consumption_proofs')]
    operations=[migrations.RunSQL('''
      CREATE FUNCTION payment_reject_exchange_cash_proof() RETURNS trigger AS $$
      DECLARE kind text;
      BEGIN
        IF TG_TABLE_NAME='payment_receipt' THEN
          SELECT order_kind INTO kind FROM customer_order WHERE id=NEW.order_id;
        ELSE
          SELECT o.order_kind INTO kind FROM aftersale_case c JOIN customer_order_line l ON l.id=c.order_line_id JOIN customer_order o ON o.id=l.order_id WHERE c.id=NEW.case_id;
        END IF;
        IF kind='POINTS' THEN RAISE EXCEPTION 'points orders have no cash receipt or refund intent'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
      CREATE TRIGGER payment_reject_exchange_cash_proof BEFORE INSERT ON payment_receipt FOR EACH ROW EXECUTE FUNCTION payment_reject_exchange_cash_proof();
      CREATE TRIGGER refund_reject_exchange_cash_proof BEFORE INSERT ON refund_intent FOR EACH ROW EXECUTE FUNCTION payment_reject_exchange_cash_proof();
    ''','''
      DROP TRIGGER refund_reject_exchange_cash_proof ON refund_intent;
      DROP TRIGGER payment_reject_exchange_cash_proof ON payment_receipt;
      DROP FUNCTION payment_reject_exchange_cash_proof();
    ''')]
