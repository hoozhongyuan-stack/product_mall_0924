"""Admin history projections: filters affect history only, never account balances."""
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .access import DiningError
from common.list_history import history_options



def history_page(rows, params, prefix, statuses):
    options=history_options(params,prefix,statuses,DiningError)
    def matches(row):
        if options['status'] and row['status']!=options['status']:return False
        if options['search'] and options['search'].casefold() not in ' '.join(str(row.get(k,'')) for k in ('id','orderNo','tableNumber','reason','paymentReference','payeeName')).casefold():return False
        if options['start'] or options['end']:
            timestamp=parse_datetime(row.get('createdAt',''))
            if timestamp is None:return False
            day=timezone.localtime(timestamp).date() if timezone.is_aware(timestamp) else timestamp.date()
            if options['start'] and day<options['start']:return False
            if options['end'] and day>options['end']:return False
        return True
    filtered=[row for row in rows if matches(row)]
    start=(options['page']-1)*options['pageSize']
    return {'items':filtered[start:start+options['pageSize']],'total':len(filtered),'page':options['page'],'pageSize':options['pageSize']}
