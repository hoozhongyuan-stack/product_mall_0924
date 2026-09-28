"""Bounded identity namespace facts for application configuration guards."""
from .models import Member


def member_app_ids():
    return list(Member.objects.order_by('wechat_app_id').values_list('wechat_app_id', flat=True).distinct()[:2])
