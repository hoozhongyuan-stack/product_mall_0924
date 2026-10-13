"""Reserve every new redemption UUID without rewriting immutable historical events.

Pre-governance USE events may legitimately share a UUID across vouchers and
receive an empty digest from AddField. The partial unique index preserves those
facts; a BEFORE INSERT guard requires a SHA-256 digest on every new USE event.
Service-level global-key lookup treats historical keys as occupied and refuses
replay when the original request cannot be identified.
"""
from django.db import migrations, models


FORWARD = r"""
CREATE FUNCTION fg_redemption_request_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.kind='USE' THEN
  IF NEW.request_digest IS NULL OR NEW.request_digest !~ '^[0-9a-f]{64}$' THEN
   RAISE EXCEPTION 'new redemption requires a SHA256 request identity';
  END IF;
  IF EXISTS(SELECT 1 FROM fulfillment_redeem_event e WHERE e.kind='USE' AND e.request_key=NEW.request_key) THEN
   RAISE EXCEPTION 'redemption request identity is already occupied';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER fg_redemption_request_identity BEFORE INSERT ON fulfillment_redeem_event
FOR EACH ROW EXECUTE FUNCTION fg_redemption_request_identity_guard();
"""
REVERSE = r"""
DROP TRIGGER fg_redemption_request_identity ON fulfillment_redeem_event;
DROP FUNCTION fg_redemption_request_identity_guard();
"""


class Migration(migrations.Migration):
    dependencies = [('fulfillment', '0011_governance_guards')]
    operations = [
        migrations.AddField(model_name='redeemevent', name='request_digest',
            field=models.CharField(max_length=64, blank=True, default=''), preserve_default=False),
        migrations.AddConstraint(model_name='redeemevent', constraint=models.UniqueConstraint(
            fields=['request_key'], condition=models.Q(kind='USE') & ~models.Q(request_digest=''),
            name='redeem_use_request_global_unique')),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
