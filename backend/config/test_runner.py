"""Django test runner that repairs reference rows in reused PostgreSQL DBs."""
from django.test.runner import DiscoverRunner

from .test_seed import restore_migration_seed_rows


class SeededDiscoverRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        for alias in kwargs.get("aliases") or ():
            restore_migration_seed_rows(alias)
        return old_config
