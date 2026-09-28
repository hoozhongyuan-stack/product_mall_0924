"""One bounded Phase C maintenance tick for an external scheduler."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from fulfillment.service import auto_confirm_receipts
from orders.service import close_expired_orders
from payments.wechat_config import WechatGatewayError, load_wechat_config


class Command(BaseCommand):
    help = "Run one bounded order-close, receipt and configured WeChat compensation tick."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 500:
            raise CommandError("limit must be between 1 and 500")
        outcomes = {}
        for label, operation in (("closed", close_expired_orders),
                                 ("auto_confirmed", auto_confirm_receipts)):
            try:
                outcomes[label] = str(operation(limit))
            except Exception as exc:
                outcomes[label] = "FAILED"
                self.stderr.write(f"{label}=FAILED errorType={type(exc).__name__}")
        try:
            load_wechat_config()
        except WechatGatewayError:
            outcomes["wechat"] = "SKIPPED_UNCONFIGURED"
        else:
            try:
                call_command("reconcile_wechat_payments", limit=limit, stdout=StringIO())
                outcomes["wechat"] = "RUN"
            except Exception as exc:
                outcomes["wechat"] = "FAILED"
                self.stderr.write(f"wechat=FAILED errorType={type(exc).__name__}")
        summary = " ".join(f"{key}={value}" for key, value in outcomes.items())
        self.stdout.write(summary)
        if "FAILED" in outcomes.values():
            raise CommandError("Phase C maintenance failed; inspect task error output and retry the tick.")
