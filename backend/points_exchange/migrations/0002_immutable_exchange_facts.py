from django.db import migrations

class Migration(migrations.Migration):
    dependencies=[('points_exchange','0001_initial'),('benefits','0002_immutable_points_event')]
    operations=[migrations.RunSQL('''
      CREATE FUNCTION exchange_offer_guard() RETURNS trigger AS $$
      BEGIN
        IF NEW.sku_id IS DISTINCT FROM OLD.sku_id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
          RAISE EXCEPTION 'exchange offer identity is immutable'; END IF;
        IF ROW(NEW.points_price,NEW.status) IS DISTINCT FROM ROW(OLD.points_price,OLD.status)
           AND NEW.revision != OLD.revision+1 THEN RAISE EXCEPTION 'exchange offer revision required'; END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
      CREATE TRIGGER exchange_offer_guard BEFORE UPDATE ON points_exchange_offer FOR EACH ROW EXECUTE FUNCTION exchange_offer_guard();
      CREATE TRIGGER exchange_quote_immutable BEFORE UPDATE OR DELETE ON points_exchange_quote FOR EACH ROW EXECUTE FUNCTION benefit_points_event_reject_mutation();
      CREATE TRIGGER exchange_operation_immutable BEFORE UPDATE OR DELETE ON points_exchange_operation FOR EACH ROW EXECUTE FUNCTION benefit_points_event_reject_mutation();
    ''','''
      DROP TRIGGER exchange_operation_immutable ON points_exchange_operation;
      DROP TRIGGER exchange_quote_immutable ON points_exchange_quote;
      DROP TRIGGER exchange_offer_guard ON points_exchange_offer;
      DROP FUNCTION exchange_offer_guard();
    ''')]
