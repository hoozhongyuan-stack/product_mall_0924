"""Create a zero points account when a member identity is first created."""

from django.db.models.signals import post_save


def _create_member_account(sender, instance, created, **kwargs):
    if created:
        from .models import PointsAccount

        PointsAccount.objects.get_or_create(member=instance)


def connect_member_account_signal():
    from customers.models import Member

    post_save.connect(_create_member_account, sender=Member,
                      dispatch_uid="benefits.create_member_account")
