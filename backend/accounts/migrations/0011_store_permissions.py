from django.db import migrations


def create_store_group(apps,schema_editor):
    alias=schema_editor.connection.alias
    Group=apps.get_model('accounts','PermissionGroup')
    Permission=apps.get_model('accounts','GroupPermission')
    group,created=Group.objects.using(alias).get_or_create(code='store_operator',defaults={'name':'门店管理'})
    if created:
        Permission.objects.using(alias).bulk_create([Permission(group=group,code=code) for code in ['stores.read','stores.manage','stores.accounts.read']])


class Migration(migrations.Migration):
    dependencies=[('accounts','0010_code_release_read_quota')]
    operations=[migrations.RunPython(create_store_group,migrations.RunPython.noop)]
