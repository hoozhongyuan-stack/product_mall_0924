from django.db import migrations


def seed(apps, schema_editor):
    Page = apps.get_model('pages', 'MicroPage')
    Selection = apps.get_model('pages', 'HomeSelection')
    original = Page.objects.using(schema_editor.connection.alias).get(page_type='HOME')
    Selection.objects.using(schema_editor.connection.alias).create(id=1, page_id=original.pk)


class Migration(migrations.Migration):
    dependencies = [('pages', '0011_home_selection')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
