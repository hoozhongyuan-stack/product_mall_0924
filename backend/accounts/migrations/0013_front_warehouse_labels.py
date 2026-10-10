from django.db import migrations


def rename_labels(apps, schema_editor):
    group = apps.get_model('accounts', 'PermissionGroup')
    rows = group.objects.using(schema_editor.connection.alias)
    rows.filter(code='store_operator', name='门店管理').update(name='前置仓管理')
    rows.filter(code='store_finance_operator', name='门店资金审核').update(name='前置仓资金审核')


def restore_labels(apps, schema_editor):
    group = apps.get_model('accounts', 'PermissionGroup')
    rows = group.objects.using(schema_editor.connection.alias)
    rows.filter(code='store_operator', name='前置仓管理').update(name='门店管理')
    rows.filter(code='store_finance_operator', name='前置仓资金审核').update(name='门店资金审核')


class Migration(migrations.Migration):
    dependencies = [('accounts', '0012_store_finance_permission')]
    operations = [migrations.RunPython(rename_labels, restore_labels)]
