from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from customers.models import MemberSession, WechatCodeUse, WechatLoginAttempt


class Command(BaseCommand):
    help = "Remove expired mini-program authentication records in small batches."

    def handle(self, *args, **options):
        now = timezone.now()
        targets = (
            (WechatLoginAttempt, "id", "created_at", now - timedelta(days=1)),
            (WechatCodeUse, "code_digest", "created_at", now - timedelta(days=30)),
            (MemberSession, "id", "expires_at", now - timedelta(days=30)),
        )
        for model, key, date_field, cutoff in targets:
            deleted = 0
            while True:
                ids = list(model.objects.filter(**{f"{date_field}__lt": cutoff})
                           .order_by(date_field).values_list(key, flat=True)[:1000])
                if not ids:
                    break
                model.objects.filter(**{f"{key}__in": ids}).delete()
                deleted += len(ids)
            self.stdout.write(f"{model._meta.label}: {deleted}")
