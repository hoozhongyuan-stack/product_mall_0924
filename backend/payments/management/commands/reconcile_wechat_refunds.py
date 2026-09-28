"""Bounded provider queries only; never dispatches a refund or opens a gate."""
from django.core.management.base import BaseCommand, CommandError
from payments.refunds import RefundError
from payments.wechat_config import WechatGatewayError
from payments.wechat_refund_service import reconcile_wechat_refunds


class Command(BaseCommand):
    help = "Query a bounded batch of pending/unknown WeChat refunds (no refund dispatch)."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args, **options):
        try:
            counts = reconcile_wechat_refunds(options["batch_size"])
        except (RefundError, WechatGatewayError) as exc:
            raise CommandError(f"{exc.code}: {exc}") from None
        if counts["failed"]:
            raise CommandError(f"WECHAT_REFUND_RECONCILE_FAILED: checked={counts['checked']} failed={counts['failed']}")
        self.stdout.write(" ".join(f"{key}={value}" for key, value in counts.items()))
