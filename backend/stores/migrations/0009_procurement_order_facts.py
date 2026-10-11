from django.db import migrations

SQL="""
CREATE FUNCTION procurement_order_facts() RETURNS trigger AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'procurement order cannot be deleted'; END IF;
 IF (NEW.id,NEW.store_id,NEW.member_id,NEW.request_key,NEW.request_digest,NEW.goods_amount_fen,NEW.note,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.store_id,OLD.member_id,OLD.request_key,OLD.request_digest,OLD.goods_amount_fen,OLD.note,OLD.created_at) THEN
 RAISE EXCEPTION 'procurement order facts are frozen'; END IF;
 IF (NEW.freight_fen,NEW.encrypted_instructions) IS DISTINCT FROM (OLD.freight_fen,OLD.encrypted_instructions) AND NOT (OLD.status='REVIEW' AND NEW.status='QUOTE') THEN
 RAISE EXCEPTION 'procurement quote is frozen'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER procurement_order_facts_guard BEFORE UPDATE OR DELETE ON stores_procurementorder FOR EACH ROW EXECUTE FUNCTION procurement_order_facts();
"""


class Migration(migrations.Migration):
    dependencies=[('stores','0008_procurement_frozen_facts')]
    operations=[migrations.RunSQL(SQL,'DROP TRIGGER procurement_order_facts_guard ON stores_procurementorder; DROP FUNCTION procurement_order_facts();')]
