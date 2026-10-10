from django.db import migrations


def create_finance_group(apps,schema_editor):
    alias=schema_editor.connection.alias
    Group=apps.get_model('accounts','PermissionGroup')
    Permission=apps.get_model('accounts','GroupPermission')
    group,created=Group.objects.using(alias).get_or_create(code='store_finance_operator',defaults={'name':'门店资金审核'})
    if created:
        Permission.objects.using(alias).bulk_create([Permission(group=group,code=code) for code in ['stores.accounts.read','stores.accounts.manage']])


class Migration(migrations.Migration):
    dependencies=[('accounts','0011_store_permissions')]
    operations=[migrations.RunPython(create_finance_group,migrations.RunPython.noop)]
