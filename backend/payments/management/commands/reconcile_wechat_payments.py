"""One bounded compensation batch, intended for an externally managed scheduler."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from payments.models import WechatPaymentAttempt
from payments.service import PaymentError
from payments.wechat_config import load_wechat_config, WechatGatewayError
from payments.wechat_service import reconcile_attempt


class Command(BaseCommand):
    help = 'Query pending WeChat payments and retry remote closing; does not enable payment gates.'

    def add_arguments(self, parser):
        parser.add_argument('--limit',type=int,default=100)

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 1000:
            raise CommandError('limit must be between 1 and 1000')
        try:
            load_wechat_config()
        except WechatGatewayError:
            raise CommandError('WeChat payment is not configured; no provider calls made.') from None
        ids = list(WechatPaymentAttempt.objects.filter(next_check_at__lte=timezone.now())
                   .exclude(state__in=['PAID','ANOMALY','CLOSED'])
                   .order_by('next_check_at','id').values_list('id',flat=True)[:options['limit']])
        succeeded = failed = 0
        for attempt_id in ids:
            try:
                reconcile_attempt(attempt_id)
                succeeded += 1
            except PaymentError:
                failed += 1
        self.stdout.write(f'wechat reconciled: {succeeded}; deferred: {failed}')
