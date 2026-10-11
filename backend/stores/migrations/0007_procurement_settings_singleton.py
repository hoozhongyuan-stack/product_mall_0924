from django.db import migrations


def create(apps,schema_editor):
    apps.get_model('stores','ProcurementSettings').objects.using(schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies=[('stores','0006_procurementproof_procurementorder_procurementevent_and_more')]
    operations=[migrations.RunPython(create,migrations.RunPython.noop)]
