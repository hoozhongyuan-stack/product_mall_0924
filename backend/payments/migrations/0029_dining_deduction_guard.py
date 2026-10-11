from django.db import migrations

SQL="""
CREATE OR REPLACE FUNCTION store_income_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'store income cannot be deleted'; END IF;
 IF OLD.status='SETTLED' OR NEW.order_id<>OLD.order_id OR NEW.store_id<>OLD.store_id OR
    NEW.dining_commission_fen<>OLD.dining_commission_fen
 THEN RAISE EXCEPTION 'settled store income and dining deduction evidence are immutable'; END IF;
 RETURN NEW;
END; $$;
"""
REVERSE="""
CREATE OR REPLACE FUNCTION store_income_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'store income cannot be deleted'; END IF;
 IF OLD.status='SETTLED' OR NEW.order_id<>OLD.order_id OR NEW.store_id<>OLD.store_id THEN RAISE EXCEPTION 'settled store income is immutable'; END IF;
 RETURN NEW;
END; $$;
"""


class Migration(migrations.Migration):
    dependencies=[('payments','0028_storeincome_dining_commission_fen_and_more'),('dining_alliance','0006_diningordersnapshot_funding_source')]
    operations=[migrations.RunSQL(SQL,REVERSE)]
