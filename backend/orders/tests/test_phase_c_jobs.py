from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from payments.wechat_config import WechatGatewayError


class PhaseCJobTests(SimpleTestCase):
    def test_one_bounded_run_skips_unconfigured_wechat(self):
        output = StringIO()
        with patch("orders.management.commands.run_phase_c_jobs.close_expired_orders", return_value=2) as close, \
             patch("orders.management.commands.run_phase_c_jobs.auto_confirm_receipts", return_value=1) as receipt, \
             patch("orders.management.commands.run_phase_c_jobs.load_wechat_config", side_effect=WechatGatewayError("unconfigured")):
            call_command("run_phase_c_jobs", limit=25, stdout=output)
        close.assert_called_once_with(25)
        receipt.assert_called_once_with(25)
        self.assertIn("wechat=SKIPPED_UNCONFIGURED", output.getvalue())
        self.assertIn("closed=2", output.getvalue())
        self.assertIn("auto_confirmed=1", output.getvalue())

    def test_failure_sets_nonzero_result_after_other_jobs_run(self):
        with patch("orders.management.commands.run_phase_c_jobs.close_expired_orders", side_effect=RuntimeError), \
             patch("orders.management.commands.run_phase_c_jobs.auto_confirm_receipts", return_value=3) as receipt, \
             patch("orders.management.commands.run_phase_c_jobs.load_wechat_config", side_effect=WechatGatewayError("unconfigured")):
            with self.assertRaises(CommandError):
                call_command("run_phase_c_jobs", limit=10, stdout=StringIO())
        receipt.assert_called_once_with(10)

    def test_limit_is_bounded(self):
        with patch("orders.management.commands.run_phase_c_jobs.close_expired_orders") as close:
            for limit in (0, 501, 1001):
                with self.assertRaisesMessage(CommandError, "limit must be between 1 and 500"):
                    call_command("run_phase_c_jobs", limit=limit, stdout=StringIO())
        close.assert_not_called()

    def test_configured_wechat_job_runs_and_failed_tick_can_be_retried(self):
        with patch("orders.management.commands.run_phase_c_jobs.close_expired_orders", return_value=0), \
             patch("orders.management.commands.run_phase_c_jobs.auto_confirm_receipts", side_effect=[RuntimeError(), 0]), \
             patch("orders.management.commands.run_phase_c_jobs.load_wechat_config"), \
             patch("orders.management.commands.run_phase_c_jobs.call_command") as reconcile:
            with self.assertRaises(CommandError):
                call_command("run_phase_c_jobs", limit=5, stdout=StringIO())
            output = StringIO()
            call_command("run_phase_c_jobs", limit=5, stdout=output)
        self.assertEqual(reconcile.call_count, 2)
        self.assertIn("wechat=RUN", output.getvalue())
        self.assertIn("auto_confirmed=0", output.getvalue())
