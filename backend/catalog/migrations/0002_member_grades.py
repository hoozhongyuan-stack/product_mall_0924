from django.db import migrations


DEFAULT_GRADES = [("normal", "普通会员", 0), ("silver", "银卡", 1), ("gold", "金卡", 2)]


def add_grades(apps, schema_editor):
    Grade = apps.get_model("catalog", "MemberGrade")
    for code, name, rank in DEFAULT_GRADES:
        Grade.objects.using(schema_editor.connection.alias).get_or_create(
            code=code, defaults={"name": name, "rank": rank}
        )


def remove_grades(apps, schema_editor):
    Grade = apps.get_model("catalog", "MemberGrade")
    Price = apps.get_model("catalog", "SkuGradePrice")
    for code, _, _ in DEFAULT_GRADES:
        grade = Grade.objects.using(schema_editor.connection.alias).filter(code=code).first()
        if grade and not Price.objects.using(schema_editor.connection.alias).filter(grade_id=grade.pk).exists():
            grade.delete()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(add_grades, remove_grades)]
