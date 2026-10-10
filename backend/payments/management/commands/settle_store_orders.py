from django.core.management.base import BaseCommand,CommandError
from payments.store_settlement import scan_settlements
from stores.access import StoreError


class Command(BaseCommand):
    help='Settle completed store orders after their snapshotted aftersale period.'
    def add_arguments(self,parser):parser.add_argument('--limit',type=int,default=100)
    def handle(self,*args,**options):
        try:rows=scan_settlements(options['limit'])
        except StoreError as exc:raise CommandError(str(exc)) from exc
        self.stdout.write(f'Processed {len(rows)} completed orders; settled {sum(row.status=="SETTLED" for row in rows)}; held {sum(row.status=="HELD" for row in rows)}.')
