"""Private admin configuration: confirmed, revisioned, and never a transaction gateway."""
from datetime import timedelta
from math import ceil

from django.db import transaction
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_cookie

from accounts.models import AdminAccount, AuditLog
from accounts.read_rate_limit import read_rate_limit
from accounts.security import audit, confirm_action, require, require_live
from common.http import error, method, parse_json_object, response

from .credentials import CredentialsUnavailable, effective_credentials, integration_row, snapshot_fingerprint
from .probe import check_credentials
from .service import (IntegrationError, check_revision, configuration_data, expected_revision,
                      update_credentials, validate_save)

OBJECT_ID = 'wechat-mini-program'


def _access(request, *, live=False, write=False):
    check = require_live if live else require
    actor, bad = check(request, 'wechat.integration.read')
    if not bad and write:
        actor, bad = check(request, 'wechat.integration.manage')
    return actor, bad


def _failure(request, exc):
    if isinstance(exc, CredentialsUnavailable):
        return error(request, 503, 'CREDENTIALS_UNAVAILABLE', str(exc))
    return error(request, exc.status, exc.code, str(exc), exc.details)


def _limit(request, actor, action):
    minutes, maximum = (5, 5) if action == 'test' else (60, 30)
    since = timezone.now() - timedelta(minutes=minutes)
    events = AuditLog.objects.filter(actor=actor, action_code='wechat.integration.'+action,
                                    occurred_at__gte=since).order_by('occurred_at')
    if events.count() >= maximum:
        oldest = events.values_list('occurred_at', flat=True).first()
        result = error(request, 429, 'RATE_LIMITED', '操作频率过高，请稍后再试。')
        result['Retry-After'] = str(max(1, ceil((oldest + timedelta(minutes=minutes)-timezone.now()).total_seconds())))
        return result


def _confirmed(request, actor, row, action):
    AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
    actor, bad = _access(request, live=True, write=True)
    if bad:
        return actor, bad
    bad = _limit(request, actor, action)
    if bad:
        return actor, bad
    return actor, confirm_action(request, actor, 'wechat.integration.'+action, OBJECT_ID, row.revision)


@cache_control(private=True, no_store=True)
@vary_on_cookie
def settings_view(request):
    bad = method(request, 'GET', 'PUT')
    if bad:
        return bad
    actor, bad = _access(request, write=request.method == 'PUT')
    if bad:
        return bad
    try:
        if request.method == 'GET':
            bad = read_rate_limit(request, actor, 'wechat.integration')
            if bad:
                return bad
            return response(request, configuration_data(integration_row()))
        body = parse_json_object(request, max_bytes=8192)
        with transaction.atomic():
            row = integration_row(lock=True)
            snapshot = effective_credentials(row)
            app_id, secret = validate_save(body, snapshot)
            check_revision(row, body['expectedRevision'])
            actor, bad = _confirmed(request, actor, row, 'update')
            if bad:
                return bad
            before = {'revision': row.revision, 'source': snapshot.source}
            update_credentials(row, snapshot, app_id, secret, actor)
            audit(request, 'wechat.integration.update', 'wechat_integration', OBJECT_ID, actor,
                  before=before, after={'revision': row.revision, 'source': 'MANAGED'})
            return response(request, configuration_data(row))
    except (CredentialsUnavailable, IntegrationError) as exc:
        return _failure(request, exc)
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '请发送不超过 8 KB 的有效 JSON 对象。')


@cache_control(private=True, no_store=True)
@vary_on_cookie
def test_view(request):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = _access(request, write=True)
    if bad:
        return bad
    try:
        body = parse_json_object(request, max_bytes=8192)
        if set(body) != {'expectedRevision'}:
            raise IntegrationError('凭据校验仅接受配置修订号。')
        expected = expected_revision(body)
        with transaction.atomic():
            row = integration_row(lock=True)
            check_revision(row, expected)
            snapshot = effective_credentials(row)
            if not snapshot.app_id or not snapshot.secret:
                raise IntegrationError('请先保存完整的小程序凭据。', 'CREDENTIALS_NOT_CONFIGURED', 409)
            actor, bad = _confirmed(request, actor, row, 'test')
            if bad:
                return bad
            audit(request, 'wechat.integration.test', 'wechat_integration', OBJECT_ID, actor,
                  after={'revision': row.revision, 'status': 'PENDING'})
        result = check_credentials(snapshot)
        with transaction.atomic():
            row = integration_row(lock=True)
            check_revision(row, expected)
            if effective_credentials(row) != snapshot:
                raise IntegrationError('配置已变化，请重新读取后确认。', 'REVISION_CONFLICT', 409)
            AdminAccount.objects.select_for_update().filter(pk=actor.pk).first()
            actor, bad = _access(request, live=True, write=True)
            if bad:
                return bad
            row.last_check_revision, row.last_check_at = row.revision, timezone.now()
            row.last_check_status, row.last_check_code = result.status, result.code
            row.last_check_fingerprint = snapshot_fingerprint(snapshot)
            row.save(update_fields=['last_check_revision', 'last_check_at', 'last_check_status', 'last_check_code',
                                    'last_check_fingerprint'])
            audit(request, 'wechat.integration.test.result', 'wechat_integration', OBJECT_ID, actor,
                  after={'revision': row.revision, 'status': result.status})
            return response(request, configuration_data(row))
    except (CredentialsUnavailable, IntegrationError) as exc:
        return _failure(request, exc)
    except ValueError:
        return error(request, 400, 'VALIDATION_FAILED', '请发送不超过 8 KB 的有效 JSON 对象。')
