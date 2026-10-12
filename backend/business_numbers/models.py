from django.db import models


class BusinessNumberCounter(models.Model):
    """One database-serialized sequence per business type and Shanghai day."""
    kind = models.CharField(max_length=2)
    day = models.DateField()
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'business_number_counter'
        constraints = [
            models.UniqueConstraint(fields=['kind', 'day'], name='business_number_type_day'),
            models.CheckConstraint(condition=models.Q(sequence__lte=999999), name='business_number_sequence_bound'),
            models.CheckConstraint(condition=models.Q(kind__in=['XS','CY','JH','JF','SH','TK']), name='business_number_kind_valid'),
        ]
