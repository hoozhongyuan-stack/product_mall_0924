from django.db import migrations


def grant_order_service(apps, schema_editor):
    using = schema_editor.connection.alias
    Group = apps.get_model("accounts", "PermissionGroup")
    Permission = apps.get_model("accounts", "GroupPermission")
    group = Group.objects.using(using).filter(code="order_service").first()
    if group:
        for code in ("order.read", "fulfillment.read", "fulfillment.ship", "fulfillment.redeem"):
            Permission.objects.using(using).get_or_create(group=group, code=code)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_inventory_permissions")]
    operations = [migrations.RunPython(grant_order_service, migrations.RunPython.noop)]
