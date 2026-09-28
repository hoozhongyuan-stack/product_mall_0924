from django.db import migrations


DEFAULT_CONFIG = {
    "schemaVersion": 1, "pageType": "HOME",
    "theme": {"pageBackgroundColor": "#F7F5F1", "headerBackgroundColor": "#FFFFFF",
              "brandTextColor": "#25221F"},
    "components": [{"componentId": "home-search", "type": "SEARCH", "sortOrder": 10,
                    "visible": True, "props": {"placeholder": "搜索商品"}}],
}


def seed_home(apps, schema_editor):
    page_model = apps.get_model("pages", "MicroPage")
    publication_model = apps.get_model("pages", "PagePublication")
    page, _ = page_model.objects.using(schema_editor.connection.alias).get_or_create(
        page_type="HOME", defaults={"name": "首页", "draft_config": DEFAULT_CONFIG})
    publication_model.objects.using(schema_editor.connection.alias).get_or_create(page_id=page.id)


class Migration(migrations.Migration):
    dependencies = [("pages", "0001_initial")]
    operations = [migrations.RunPython(seed_home, migrations.RunPython.noop)]
