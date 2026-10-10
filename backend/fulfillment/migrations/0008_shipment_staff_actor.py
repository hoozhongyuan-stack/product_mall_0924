import importlib
from django.db import migrations, models
import django.db.models.deletion
old = importlib.import_module('fulfillment.migrations.0003_evidence_guards').FORWARD
start = old.index('CREATE FUNCTION fulfillment_guard_shipment()')
end = old.index('CREATE TRIGGER fulfillment_shipment_guard', start)
old_function = old[start:end].replace('CREATE FUNCTION', 'CREATE OR REPLACE FUNCTION', 1)
function = old_function.replace('NEW.shipped_by_id IS DISTINCT FROM OLD.shipped_by_id OR', 'NEW.shipped_by_id IS DISTINCT FROM OLD.shipped_by_id OR\n      NEW.shipped_by_member_id IS DISTINCT FROM OLD.shipped_by_member_id OR')
class Migration(migrations.Migration):
    dependencies = [('fulfillment','0007_store_handoff_guards'), ('customers','0008_member_profile_quota')]
    operations = [
        migrations.AlterField(model_name='shipment', name='shipped_by', field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='shipments',to='accounts.adminaccount')),
        migrations.AddField(model_name='shipment',name='shipped_by_member',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='store_shipments',to='customers.member')),
        migrations.AddConstraint(model_name='shipment',constraint=models.CheckConstraint(condition=(models.Q(shipped_by__isnull=False,shipped_by_member__isnull=True)|models.Q(shipped_by__isnull=True,shipped_by_member__isnull=False)),name='shipment_actor_exactly_one')),
        migrations.RunSQL(function, old_function),
    ]
