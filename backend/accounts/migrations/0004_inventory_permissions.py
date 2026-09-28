from django.db import migrations


CODES = ("inventory.read", "inventory.manage")


def grant_inventory_group(apps, schema_editor):
    Group = apps.get_model("accounts", "PermissionGroup")
    Permission = apps.get_model("accounts", "GroupPermission")
    group = Group.objects.using(schema_editor.connection.alias).filter(code="inventory_operator").first()
    if group:
        for code in CODES:
            Permission.objects.using(schema_editor.connection.alias).get_or_create(group=group, code=code)


def revoke_inventory_group(apps, schema_editor):
    Group = apps.get_model("accounts", "PermissionGroup")
    Permission = apps.get_model("accounts", "GroupPermission")
    group = Group.objects.using(schema_editor.connection.alias).filter(code="inventory_operator").first()
    if group:
        Permission.objects.using(schema_editor.connection.alias).filter(group=group, code__in=CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_actionconfirmation")]
    operations = [migrations.RunPython(grant_inventory_group, revoke_inventory_group)]
