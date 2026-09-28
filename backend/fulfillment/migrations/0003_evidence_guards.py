from django.db import migrations


FORWARD = """
CREATE FUNCTION fulfillment_reject_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fulfillment evidence is immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER fulfillment_snapshot_immutable
  BEFORE UPDATE OR DELETE ON fulfillment_order_snapshot
  FOR EACH ROW EXECUTE FUNCTION fulfillment_reject_mutation();
CREATE TRIGGER fulfillment_correction_immutable
  BEFORE UPDATE OR DELETE ON fulfillment_shipment_correction
  FOR EACH ROW EXECUTE FUNCTION fulfillment_reject_mutation();
CREATE TRIGGER fulfillment_redeem_event_immutable
  BEFORE UPDATE OR DELETE ON fulfillment_redeem_event
  FOR EACH ROW EXECUTE FUNCTION fulfillment_reject_mutation();

CREATE FUNCTION fulfillment_guard_voucher() RETURNS trigger AS $$
DECLARE purchased integer;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'redeem voucher is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
      NEW.order_line_id IS DISTINCT FROM OLD.order_line_id OR
      NEW.nonce IS DISTINCT FROM OLD.nonce OR
      NEW.code_digest IS DISTINCT FROM OLD.code_digest OR
      NEW.valid_until IS DISTINCT FROM OLD.valid_until OR
      NEW.issued_at IS DISTINCT FROM OLD.issued_at) THEN
    RAISE EXCEPTION 'redeem voucher identity is immutable';
  END IF;
  SELECT quantity INTO purchased FROM customer_order_line WHERE id = NEW.order_line_id;
  IF purchased IS NULL OR NEW.redeemed_quantity + NEW.voided_quantity > purchased THEN
    RAISE EXCEPTION 'redeem quantity exceeds purchased quantity';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER fulfillment_voucher_guard
  BEFORE INSERT OR UPDATE OR DELETE ON fulfillment_redeem_voucher
  FOR EACH ROW EXECUTE FUNCTION fulfillment_guard_voucher();

CREATE FUNCTION fulfillment_guard_shipment() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'shipment is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
      NEW.order_id IS DISTINCT FROM OLD.order_id OR
      NEW.warehouse_id IS DISTINCT FROM OLD.warehouse_id OR
      NEW.shipped_by_id IS DISTINCT FROM OLD.shipped_by_id OR
      NEW.shipped_at IS DISTINCT FROM OLD.shipped_at OR
      NEW.auto_confirm_days_snapshot IS DISTINCT FROM OLD.auto_confirm_days_snapshot OR
      NEW.auto_confirm_at IS DISTINCT FROM OLD.auto_confirm_at OR
      NEW.request_key IS DISTINCT FROM OLD.request_key) THEN
    RAISE EXCEPTION 'shipment origin is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
      NEW.carrier_code IS DISTINCT FROM OLD.carrier_code OR
      NEW.carrier_name IS DISTINCT FROM OLD.carrier_name OR
      NEW.tracking_no IS DISTINCT FROM OLD.tracking_no) THEN
    IF NOT EXISTS (
      SELECT 1 FROM fulfillment_shipment_correction c
      WHERE c.shipment_id = OLD.id
        AND c.old_carrier_code = OLD.carrier_code
        AND c.old_carrier_name = OLD.carrier_name
        AND c.old_tracking_no = OLD.tracking_no
        AND c.new_carrier_code = NEW.carrier_code
        AND c.new_carrier_name = NEW.carrier_name
        AND c.new_tracking_no = NEW.tracking_no
        AND c.corrected_at >= transaction_timestamp()
    ) THEN
      RAISE EXCEPTION 'shipment correction evidence required';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER fulfillment_shipment_guard
  BEFORE UPDATE OR DELETE ON fulfillment_shipment
  FOR EACH ROW EXECUTE FUNCTION fulfillment_guard_shipment();
"""

REVERSE = """
DROP TRIGGER IF EXISTS fulfillment_shipment_guard ON fulfillment_shipment;
DROP FUNCTION IF EXISTS fulfillment_guard_shipment();
DROP TRIGGER IF EXISTS fulfillment_voucher_guard ON fulfillment_redeem_voucher;
DROP FUNCTION IF EXISTS fulfillment_guard_voucher();
DROP TRIGGER IF EXISTS fulfillment_redeem_event_immutable ON fulfillment_redeem_event;
DROP TRIGGER IF EXISTS fulfillment_correction_immutable ON fulfillment_shipment_correction;
DROP TRIGGER IF EXISTS fulfillment_snapshot_immutable ON fulfillment_order_snapshot;
DROP FUNCTION IF EXISTS fulfillment_reject_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("fulfillment", "0002_seed_and_backfill")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
