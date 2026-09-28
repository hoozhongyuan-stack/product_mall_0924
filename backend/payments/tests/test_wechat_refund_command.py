"""Scheduler exit semantics are distinct from pending provider refunds."""
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from payments.wechat_config import WechatGatewayError


class WechatRefundCommandTests(SimpleTestCase):
    def run_command(self, counts):
        output = StringIO()
        with patch('payments.management.commands.reconcile_wechat_refunds.reconcile_wechat_refunds', return_value=counts) as worker:
            call_command('reconcile_wechat_refunds', batch_size=5, stdout=output)
            worker.assert_called_once_with(5)
        return output.getvalue()

    def test_disabled_skip_and_pending_exit_normally(self):
        self.assertIn('skipped=1', self.run_command({'checked': 0, 'succeeded': 0, 'pending': 0, 'failed': 0, 'skipped': 1}))
        self.assertIn('pending=2', self.run_command({'checked': 2, 'succeeded': 0, 'pending': 2, 'failed': 0}))

    def test_provider_failures_and_config_failures_exit_nonzero(self):
        with self.assertRaises(CommandError):
            self.run_command({'checked': 2, 'succeeded': 1, 'pending': 0, 'failed': 1})
        with patch('payments.management.commands.reconcile_wechat_refunds.reconcile_wechat_refunds',
            side_effect=WechatGatewayError('配置尚未具备', 'WECHAT_NOT_CONFIGURED', 503)), self.assertRaises(CommandError):
            call_command('reconcile_wechat_refunds', stdout=StringIO())
