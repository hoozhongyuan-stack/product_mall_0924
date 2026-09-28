"""Django test runner that repairs reference rows in reused PostgreSQL DBs."""
from django.test.runner import DiscoverRunner
from django.db.models.signals import post_migrate

from .test_seed import restore_migration_seed_rows, restore_wechat_integration_seed


class SeededDiscoverRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        post_migrate.connect(restore_wechat_integration_seed, dispatch_uid='test-wechat-singleton', weak=False)

    def teardown_test_environment(self, **kwargs):
        post_migrate.disconnect(dispatch_uid='test-wechat-singleton')
        super().teardown_test_environment(**kwargs)

    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        for alias in kwargs.get("aliases") or ():
            restore_migration_seed_rows(alias)
        return old_config
