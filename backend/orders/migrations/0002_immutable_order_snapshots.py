"""Reject edits to completed order facts and reservation events at the database layer."""

from django.db import migrations


CREATE = """
CREATE FUNCTION order_reject_line_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'order line snapshot is immutable';
END;
$$;
CREATE TRIGGER order_line_immutable
  BEFORE UPDATE OR DELETE ON customer_order_line
  FOR EACH ROW EXECUTE FUNCTION order_reject_line_mutation();

CREATE FUNCTION order_reject_financial_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'order snapshot is immutable';
    END IF;
    IF ROW(NEW.order_no, NEW.member_id, NEW.quote_id, NEW.payment_method,
           NEW.address_snapshot, NEW.goods_total_fen, NEW.shipping_fee_fen,
           NEW.payable_fen, NEW.expires_at, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.order_no, OLD.member_id, OLD.quote_id, OLD.payment_method,
           OLD.address_snapshot, OLD.goods_total_fen, OLD.shipping_fee_fen,
           OLD.payable_fen, OLD.expires_at, OLD.created_at) THEN
        RAISE EXCEPTION 'order snapshot is immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER order_financial_immutable
  BEFORE UPDATE OR DELETE ON customer_order
  FOR EACH ROW EXECUTE FUNCTION order_reject_financial_mutation();

CREATE FUNCTION inventory_reject_reservation_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'inventory reservation event is immutable';
END;
$$;
CREATE TRIGGER inventory_reservation_event_immutable
  BEFORE UPDATE OR DELETE ON inventory_reservation_event
  FOR EACH ROW EXECUTE FUNCTION inventory_reject_reservation_event_mutation();
"""


DROP = """
DROP TRIGGER inventory_reservation_event_immutable ON inventory_reservation_event;
DROP FUNCTION inventory_reject_reservation_event_mutation();
DROP TRIGGER order_financial_immutable ON customer_order;
DROP FUNCTION order_reject_financial_mutation();
DROP TRIGGER order_line_immutable ON customer_order_line;
DROP FUNCTION order_reject_line_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial"),
                    ("inventory", "0007_inventoryreservation_order_line_and_more")]
    operations = [migrations.RunSQL(CREATE, DROP)]
