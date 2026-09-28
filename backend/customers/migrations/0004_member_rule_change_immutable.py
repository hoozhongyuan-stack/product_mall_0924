from django.db import migrations


class Migration(migrations.Migration):
    dependencies=[('customers','0003_memberrulechange'), ('benefits','0010_benefitledger_cause_ref')]
    operations=[migrations.RunSQL('''
        CREATE TRIGGER member_rule_change_immutable BEFORE UPDATE OR DELETE ON customer_member_rule_change
        FOR EACH ROW EXECUTE FUNCTION benefit_lifecycle_reject_mutation();
    ''','DROP TRIGGER member_rule_change_immutable ON customer_member_rule_change;')]
