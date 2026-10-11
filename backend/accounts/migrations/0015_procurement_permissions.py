from django.db import migrations


def create(apps,schema_editor):
    alias=schema_editor.connection.alias
    group,created=apps.get_model('accounts','PermissionGroup').objects.using(alias).get_or_create(code='procurement_operator',defaults={'name':'前置仓进货管理'})
    if created:
        apps.get_model('accounts','GroupPermission').objects.using(alias).bulk_create([apps.get_model('accounts','GroupPermission')(group=group,code=code) for code in ['stores.procurement.read','stores.procurement.manage']])


class Migration(migrations.Migration):
    dependencies=[('accounts','0014_remove_adminreadquota_admin_read_quota_scope_and_more')]
    operations=[migrations.RunPython(create,migrations.RunPython.noop)]
