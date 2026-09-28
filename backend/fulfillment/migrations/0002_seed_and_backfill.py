"""Default ten-day policy and safe snapshots for orders made before C3."""
import base64
import hashlib
import hmac
import secrets
from django.conf import settings
from django.db import migrations


def seed(apps, schema_editor):
    using = schema_editor.connection.alias
    Policy = apps.get_model("fulfillment", "FulfillmentPolicy")
    Snapshot = apps.get_model("fulfillment", "OrderFulfillmentSnapshot")
    Voucher = apps.get_model("fulfillment", "RedeemVoucher")
    Order = apps.get_model("orders", "Order")
    Line = apps.get_model("orders", "OrderLine")
    Policy.objects.using(using).get_or_create(pk=1, defaults={"auto_confirm_days": 10, "revision": 1})
    snapshots = (Snapshot(order_id=order_id, auto_confirm_days=10, policy_revision=1)
                 for order_id in Order.objects.using(using).values_list("id", flat=True).iterator(chunk_size=500))
    Snapshot.objects.using(using).bulk_create(snapshots, ignore_conflicts=True, batch_size=500)
    for line in Line.objects.using(using).filter(order__status="PAID", fulfillment_kind="REDEEM") \
            .values("id", "redeem_valid_until").iterator(chunk_size=500):
        nonce = secrets.token_hex(16)
        source = f"C3-REDEEM:{line['id']}:{nonce}".encode("ascii")
        raw = hmac.new(settings.SECRET_KEY.encode("utf-8"), source, hashlib.sha256).digest()[:17]
        code = base64.b32encode(raw).decode("ascii").rstrip("=")[:26]
        Voucher.objects.using(using).get_or_create(order_line_id=line["id"], defaults={
            "nonce": nonce, "code_digest": hashlib.sha256(code.encode("ascii")).hexdigest(),
            "valid_until": line["redeem_valid_until"]})


class Migration(migrations.Migration):
    dependencies = [("fulfillment", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
