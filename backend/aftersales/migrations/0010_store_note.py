from django.db import migrations, models
import django.db.models.deletion
import uuid
class Migration(migrations.Migration):
    dependencies=[('aftersales','0007_d3_settlement_guards'),('customers','0008_member_profile_quota')]
    operations=[migrations.CreateModel(name='StoreAfterSaleNote',fields=[
        ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False,serialize=False)),
        ('kind',models.CharField(max_length=7,choices=[('ADVICE','Advice'),('RECEIPT','Receipt')])),
        ('note',models.CharField(max_length=500)),('received_quantity',models.PositiveIntegerField(default=0)),('salable_quantity',models.PositiveIntegerField(default=0)),
        ('request_key',models.UUIDField(unique=True)),('request_digest',models.CharField(max_length=64)),('occurred_at',models.DateTimeField(auto_now_add=True)),
        ('case',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='store_notes',to='aftersales.aftersalecase')),
        ('actor',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,to='customers.member'))],options={'db_table':'aftersale_store_note','constraints':[models.CheckConstraint(condition=models.Q(salable_quantity__lte=models.F('received_quantity')),name='store_note_salable_bounded')]}),
        migrations.RunSQL('CREATE TRIGGER store_aftersale_note_immutable BEFORE UPDATE OR DELETE ON aftersale_store_note FOR EACH ROW EXECUTE FUNCTION aftersale_immutable();','DROP TRIGGER store_aftersale_note_immutable ON aftersale_store_note;')]
