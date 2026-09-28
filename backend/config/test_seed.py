"""Restore migration-owned reference rows when a retained test DB was flushed.

``TransactionTestCase`` flushes rows after each test. With ``--keepdb`` the
schema and migration history persist, so Django does not replay data migrations
at the start of the next test invocation. This module is only called by the
test runner, never by application startup or production migrations.
"""
from importlib import import_module

from django.db import transaction


def restore_wechat_integration_seed(sender=None, using='default', **kwargs):
    """Test-runner-only post_migrate receiver; production never repairs a missing row."""
    from wechat_integration.models import MiniProgramIntegration
    MiniProgramIntegration.objects.using(using).get_or_create(pk=1)


def restore_migration_seed_rows(alias):
    from accounts.models import GroupPermission, PermissionGroup
    from catalog.models import MemberGrade
    from fulfillment.models import FulfillmentPolicy
    from pages.models import MicroPage, PagePublication, StartupConfig, StartupPublication
    from payments.models import OfflinePaymentPolicy
    from shipping.models import ShippingPolicy

    presets = import_module("accounts.migrations.0002_default_groups").PRESETS
    grades = import_module("catalog.migrations.0002_member_grades").DEFAULT_GRADES
    home_config = import_module("pages.migrations.0002_seed_home").DEFAULT_CONFIG
    extra_permissions = {
        "inventory_operator": ("inventory.read", "inventory.manage"),
        "order_service": ("order.read", "fulfillment.read", "fulfillment.ship", "fulfillment.redeem"),
    }
    with transaction.atomic(using=alias):
        for code, (name, codes) in presets.items():
            group, _ = PermissionGroup.objects.using(alias).get_or_create(
                code=code, defaults={"name": name})
            for permission in (*codes, *extra_permissions.get(code, ())):
                GroupPermission.objects.using(alias).get_or_create(group=group, code=permission)
        for code, name, rank in grades:
            MemberGrade.objects.using(alias).get_or_create(
                code=code, defaults={"name": name, "rank": rank})

        home, _ = MicroPage.objects.using(alias).get_or_create(
            page_type="HOME", defaults={"name": "首页", "draft_config": home_config})
        PagePublication.objects.using(alias).get_or_create(page=home)
        startup, _ = StartupConfig.objects.using(alias).get_or_create(pk=1)
        StartupPublication.objects.using(alias).get_or_create(config=startup)
        ShippingPolicy.objects.using(alias).get_or_create(pk=1)
        OfflinePaymentPolicy.objects.using(alias).get_or_create(pk=1)
        FulfillmentPolicy.objects.using(alias).get_or_create(pk=1)
        restore_wechat_integration_seed(using=alias)
