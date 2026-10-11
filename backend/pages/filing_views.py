"""Small shop-wide filing configuration; no client-maintained duplicate."""
from django.db import transaction
from django.views.decorators.cache import never_cache
from accounts.security import require, require_live, audit
from accounts.read_rate_limit import read_rate_limit
from common.http import method, response, error, parse_json_object
from .models import StorefrontFiling
from .validation import PageConfigError


def public_filing():
    row = StorefrontFiling.objects.filter(pk=1).first()
    return {'recordNo': row.record_no if row else ''}


@never_cache
def filing(request):
    bad = method(request, 'GET', 'PUT')
    if bad:
        return bad
    actor, bad = require(request, 'navigation.read' if request.method == 'GET' else 'navigation.edit')
    if bad:
        return bad
    limited = read_rate_limit(request, actor, 'storefront.filing')
    if limited:
        return limited
    if request.method == 'GET':
        row, _ = StorefrontFiling.objects.get_or_create(pk=1)
        return response(request, {'recordNo': row.record_no, 'revision': row.revision})
    try:
        values = parse_json_object(request, max_bytes=2048, error_type=PageConfigError)
        if (set(values) != {'expectedRevision', 'recordNo'} or type(values['expectedRevision']) is not int or values['expectedRevision'] < 1 or
                not isinstance(values['recordNo'], str) or len(values['recordNo']) > 100 or any(ord(char) < 32 for char in values['recordNo'])):
            raise PageConfigError('备案号或修订号无效。')
        with transaction.atomic():
            row, _ = StorefrontFiling.objects.select_for_update().get_or_create(pk=1)
            actor, bad = require_live(request, 'navigation.edit')
            if bad:
                return bad
            if row.revision != values['expectedRevision']:
                return error(request, 409, 'REVISION_CONFLICT', '备案号已修改，请刷新后重试。')
            before = {'recordNo': row.record_no, 'revision': row.revision}
            row.record_no = values['recordNo'].strip()
            row.revision += 1
            row.save(update_fields=['record_no', 'revision', 'updated_at'])
            after = {'recordNo': row.record_no, 'revision': row.revision}
            audit(request, 'storefront.filing.update', 'storefront_filing', 1, actor, before=before, after=after)
        return response(request, after)
    except PageConfigError as exc:
        return error(request, exc.status, exc.code, str(exc))
