"""Recovery command exposes bounded local work and an actionable exit status."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from payments.refunds import RefundError


class RefundRecoveryCommandTests(SimpleTestCase):
    def test_success_prints_counts_and_preserves_requested_limit(self):
        output = StringIO()
        result = {"succeeded": 1, "failed": 0, "anomalies": 0}
        with patch("payments.management.commands.recover_recorded_refunds.recover_recorded_refunds",
                   return_value=result) as recover:
            call_command("recover_recorded_refunds", limit=5, stdout=output)
        recover.assert_called_once_with(5)
        self.assertIn("succeeded=1 failed=0 anomalies=0", output.getvalue())

    def test_unresolved_results_exit_with_error(self):
        for result in ({"failed": 1, "anomalies": 0}, {"failed": 0, "anomalies": 1}):
            with self.subTest(result=result), patch(
                    "payments.management.commands.recover_recorded_refunds.recover_recorded_refunds",
                    return_value=result), self.assertRaises(CommandError):
                call_command("recover_recorded_refunds", stdout=StringIO())

    def test_invalid_limit_becomes_command_error(self):
        with patch("payments.management.commands.recover_recorded_refunds.recover_recorded_refunds",
                   side_effect=RefundError("退款恢复批次范围不正确。")), self.assertRaises(CommandError):
            call_command("recover_recorded_refunds", limit=0, stdout=StringIO())
