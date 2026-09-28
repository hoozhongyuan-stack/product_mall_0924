import uuid
from django.db import migrations, models
import django.db.models.deletion

FORWARD = """
CREATE TRIGGER fulfillment_voucher_refund_immutable
 BEFORE UPDATE OR DELETE ON fulfillment_voucher_refund_event
 FOR EACH ROW EXECUTE FUNCTION fulfillment_reject_mutation();

CREATE FUNCTION fulfillment_guard_refund_void() RETURNS trigger AS $$
BEGIN
 IF NEW.voided_quantity < OLD.voided_quantity THEN
   RAISE EXCEPTION 'refunded voucher capacity cannot be restored';
 END IF;
 IF NEW.voided_quantity > OLD.voided_quantity AND NEW.voided_quantity <> (
   SELECT COALESCE(SUM(e.quantity), 0) FROM fulfillment_voucher_refund_event e
   WHERE e.voucher_id=NEW.id
 ) THEN
   RAISE EXCEPTION 'voucher refund evidence required';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER fulfillment_refund_void_guard
 BEFORE UPDATE ON fulfillment_redeem_voucher
 FOR EACH ROW EXECUTE FUNCTION fulfillment_guard_refund_void();
"""
REVERSE = """
DROP TRIGGER IF EXISTS fulfillment_refund_void_guard ON fulfillment_redeem_voucher;
DROP FUNCTION IF EXISTS fulfillment_guard_refund_void();
DROP TRIGGER IF EXISTS fulfillment_voucher_refund_immutable ON fulfillment_voucher_refund_event;
"""


class Migration(migrations.Migration):
    dependencies = [("fulfillment", "0004_tracking_snapshot")]
    operations = [
        migrations.CreateModel(
            name="VoucherRefundEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("case_id", models.UUIDField(unique=True)),
                ("quantity", models.PositiveIntegerField()),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("voucher", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                               related_name="refund_events", to="fulfillment.redeemvoucher")),
            ],
            options={"db_table": "fulfillment_voucher_refund_event",
                     "constraints": [models.CheckConstraint(condition=models.Q(quantity__gt=0),
                                                            name="voucher_refund_quantity_positive")]},
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
