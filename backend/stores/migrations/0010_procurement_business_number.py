from zoneinfo import ZoneInfo
from django.db import migrations, models
import business_numbers.service


def assign_numbers(apps, schema_editor):
    rows = apps.get_model('stores', 'ProcurementOrder')
    counters = apps.get_model('business_numbers', 'BusinessNumberCounter')
    for row in rows.objects.order_by('created_at', 'id').iterator():
        local = row.created_at.astimezone(ZoneInfo('Asia/Shanghai'))
        counter, _ = counters.objects.get_or_create(kind='JH', day=local.date(), defaults={'sequence': 0})
        counter.sequence += 1
        if counter.sequence > 999999:
            raise ValueError('Historical procurement daily sequence exceeds limit')
        counter.save(update_fields=['sequence'])
        rows.objects.filter(pk=row.pk).update(order_no=f'JH{local:%Y%m%d%H%M%S}{counter.sequence:06d}')


GUARD = '''
CREATE FUNCTION procurement_number_guard() RETURNS trigger AS $$
BEGIN
 IF NEW.order_no IS DISTINCT FROM OLD.order_no THEN
  RAISE EXCEPTION 'procurement number is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER procurement_number_guard BEFORE UPDATE ON stores_procurementorder
 FOR EACH ROW EXECUTE FUNCTION procurement_number_guard();
'''


class Migration(migrations.Migration):
    dependencies = [('stores', '0009_procurement_order_facts'), ('business_numbers', '0001_initial')]
    operations = [
        migrations.AddField(model_name='procurementorder', name='order_no', field=models.CharField(max_length=24, null=True, unique=True, editable=False)),
        migrations.RunPython(assign_numbers, migrations.RunPython.noop),
        migrations.AlterField(model_name='procurementorder', name='order_no', field=models.CharField(max_length=24, unique=True, default=business_numbers.service.procurement_number, editable=False)),
        migrations.RunSQL(GUARD, 'DROP TRIGGER procurement_number_guard ON stores_procurementorder; DROP FUNCTION procurement_number_guard();'),
    ]
