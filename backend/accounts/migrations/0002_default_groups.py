from django.db import migrations


PRESETS = {
    "catalog_operator": ("商品运营", ["catalog.read", "catalog.write", "sku.status.write",
                            "sku.unit.write", "sku.price.write", "asset.read", "asset.upload"]),
    "order_service": ("订单售后", []),
    "inventory_operator": ("库存管理", []),
    "member_marketing": ("会员营销", ["page.read", "page.edit", "startup.read",
                            "startup.edit", "asset.read", "asset.upload"]),
}


def add_presets(apps, schema_editor):
    Group = apps.get_model("accounts", "PermissionGroup")
    Permission = apps.get_model("accounts", "GroupPermission")
    for code, (name, codes) in PRESETS.items():
        group, created = Group.objects.using(schema_editor.connection.alias).get_or_create(
            code=code, defaults={"name": name}
        )
        if created:
            Permission.objects.using(schema_editor.connection.alias).bulk_create(
                [Permission(group=group, code=permission) for permission in codes]
            )


def remove_presets(apps, schema_editor):
    Group = apps.get_model("accounts", "PermissionGroup")
    AccountGroup = apps.get_model("accounts", "AccountGroup")
    for group in Group.objects.using(schema_editor.connection.alias).filter(code__in=PRESETS):
        if not AccountGroup.objects.using(schema_editor.connection.alias).filter(group_id=group.pk).exists():
            group.delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(add_presets, remove_presets)]
