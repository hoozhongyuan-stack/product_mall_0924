from io import StringIO
from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError


class D3RecoveryCommandTests(TestCase):
    def test_invalid_bounds_and_cursor(self):
        for kwargs in [{'batch_size': 0}, {'batch_size': 501}, {'after_order_id': 'wrong'}]:
            with self.assertRaises(CommandError):
                call_command('reconcile_order_benefits', stdout=StringIO(), **kwargs)

    def test_empty_batch_without_network(self):
        output = StringIO()
        call_command('reconcile_order_benefits', stdout=output)
        self.assertIn('checked=0 applied=0 failed=0', output.getvalue())
