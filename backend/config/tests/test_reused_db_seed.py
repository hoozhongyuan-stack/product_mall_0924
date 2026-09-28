"""Regression checks for reference rows in a retained Django test database."""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase


class SeededRunnerBoundaryTests(SimpleTestCase):
    def test_suite_without_database_aliases_never_restores_seed_rows(self):
        from config.test_runner import SeededDiscoverRunner

        with patch("django.test.runner.DiscoverRunner.setup_databases", return_value={}), \
             patch("config.test_runner.restore_migration_seed_rows") as restore:
            SeededDiscoverRunner().setup_databases(aliases=set())
        restore.assert_not_called()


class ReusedDatabaseSeedTests(TestCase):
    def test_restore_missing_migration_seed_rows_without_overwriting_custom_rows(self):
        from accounts.models import GroupPermission, PermissionGroup
        from catalog.models import MemberGrade
        from config.test_seed import restore_migration_seed_rows
        from fulfillment.models import FulfillmentPolicy
        from pages.models import MicroPage, PagePublication, StartupConfig, StartupPublication
        from payments.models import OfflinePaymentPolicy
        from shipping.models import ShippingPolicy
        from django.db import connection

        PermissionGroup.objects.filter(code="catalog_operator").delete()
        MemberGrade.objects.filter(code="normal").delete()
        # Simulate Django's test-database flush, which uses TRUNCATE. Normal
        # DELETE is deliberately forbidden so live publication epochs survive.
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE page_publication, startup_publication")
        MicroPage.objects.filter(page_type="HOME").delete()
        StartupConfig.objects.filter(pk=1).delete()
        ShippingPolicy.objects.filter(pk=1).delete()
        OfflinePaymentPolicy.objects.filter(pk=1).delete()
        FulfillmentPolicy.objects.filter(pk=1).delete()
        custom = ShippingPolicy.objects.create(pk=1, fee_fen=2222)

        restore_migration_seed_rows("default")
        restore_migration_seed_rows("default")

        group = PermissionGroup.objects.get(code="catalog_operator")
        self.assertTrue(GroupPermission.objects.filter(group=group, code="catalog.read").exists())
        self.assertTrue(MemberGrade.objects.filter(code="normal").exists())
        self.assertTrue(PagePublication.objects.filter(page__page_type="HOME").exists())
        self.assertTrue(StartupPublication.objects.filter(config_id=1).exists())
        self.assertEqual(ShippingPolicy.objects.get(pk=1).fee_fen, custom.fee_fen)
        self.assertTrue(OfflinePaymentPolicy.objects.filter(pk=1).exists())
        self.assertTrue(FulfillmentPolicy.objects.filter(pk=1).exists())
