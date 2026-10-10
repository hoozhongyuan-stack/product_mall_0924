"""Bound the store surfaces, including paid upstream map requests."""
import hashlib
import hmac
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import StoreRequestQuota


def allowed(request,scope):
    source=request.META.get('REMOTE_ADDR','unknown')
    digest=hmac.new(settings.SECRET_KEY.encode(),source.encode(),hashlib.sha256).hexdigest()
    now=timezone.now()
    with transaction.atomic():
        row,_=StoreRequestQuota.objects.select_for_update().get_or_create(source_digest=digest,scope=scope,defaults={'window_start':now})
        if row.window_start<=now-timedelta(minutes=1):
            row.count=0
            row.window_start=now
        limit=20 if scope=='map_search' else 120
        if row.count>=limit:return False
        row.count+=1
        row.save(update_fields=['count','window_start'])
    return True
