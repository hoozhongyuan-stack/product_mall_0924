from zoneinfo import ZoneInfo
from django.db import migrations, models
import business_numbers.service


def assign_numbers(apps, schema_editor):
    rows = apps.get_model('aftersales', 'AfterSaleCase')
    counters = apps.get_model('business_numbers', 'BusinessNumberCounter')
    for row in rows.objects.order_by('created_at', 'id').iterator():
        local = row.created_at.astimezone(ZoneInfo('Asia/Shanghai'))
        counter, _ = counters.objects.get_or_create(kind='SH', day=local.date(), defaults={'sequence': 0})
        counter.sequence += 1
        if counter.sequence > 999999:
            raise ValueError('Historical aftersale daily sequence exceeds limit')
        counter.save(update_fields=['sequence'])
        rows.objects.filter(pk=row.pk).update(case_no=f'SH{local:%Y%m%d%H%M%S}{counter.sequence:06d}')


GUARD = '''
CREATE FUNCTION aftersale_number_guard() RETURNS trigger AS $$
BEGIN
 IF NEW.case_no IS DISTINCT FROM OLD.case_no THEN
  RAISE EXCEPTION 'aftersale number is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER aftersale_number_guard BEFORE UPDATE ON aftersale_case
 FOR EACH ROW EXECUTE FUNCTION aftersale_number_guard();
'''


class Migration(migrations.Migration):
    dependencies = [('aftersales', '0010_store_note'), ('business_numbers', '0001_initial')]
    operations = [
        migrations.AddField(model_name='aftersalecase', name='case_no', field=models.CharField(max_length=24, null=True, unique=True, editable=False)),
        migrations.RunPython(assign_numbers, migrations.RunPython.noop),
        migrations.AlterField(model_name='aftersalecase', name='case_no', field=models.CharField(max_length=24, unique=True, default=business_numbers.service.aftersale_number, editable=False)),
        migrations.RunSQL(GUARD, 'DROP TRIGGER aftersale_number_guard ON aftersale_case; DROP FUNCTION aftersale_number_guard();'),
    ]
