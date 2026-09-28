from django.db import migrations


def grant_content_editors(apps, schema_editor):
    using = schema_editor.connection.alias
    Group = apps.get_model("accounts", "PermissionGroup")
    Permission = apps.get_model("accounts", "GroupPermission")
    group = Group.objects.using(using).filter(code="member_marketing").first()
    if group:
        for code in ("navigation.read", "navigation.edit",
                     "customer_service.read", "customer_service.edit"):
            Permission.objects.using(using).get_or_create(group=group, code=code)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_fulfillment_permissions")]
    operations = [migrations.RunPython(grant_content_editors, migrations.RunPython.noop)]
