from django.db import migrations

class Migration(migrations.Migration):
    dependencies=[('payments','0019_points_orders_reject_cash_proofs')]
    operations=[migrations.RunSQL('''
      CREATE TRIGGER payment_reject_exchange_cash_binding BEFORE UPDATE OF order_id ON payment_receipt
        FOR EACH ROW EXECUTE FUNCTION payment_reject_exchange_cash_proof();
      CREATE TRIGGER refund_reject_exchange_cash_binding BEFORE UPDATE OF case_id ON refund_intent
        FOR EACH ROW EXECUTE FUNCTION payment_reject_exchange_cash_proof();
    ''','''
      DROP TRIGGER refund_reject_exchange_cash_binding ON refund_intent;
      DROP TRIGGER payment_reject_exchange_cash_binding ON payment_receipt;
    ''')]
