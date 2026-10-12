"""Revision-fenced, audited home identity independent of page publication."""
import hashlib
import json

from django.db import transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie
from common.http import method, parse_json_object
from accounts.security import audit, confirm_action, error, response
from catalog.validation import CatalogError
from .history import idempotency_key, publication_revision, require_content
from .models import HomeSelection, HomeSelectionRequest, MicroPage, PagePublication
from .validation import PageConfigError, validate_config
from .runtime import check_runtime


def current_page_id():
    return HomeSelection.objects.values_list('page_id', flat=True).get(pk=1)


def selection_data(selection):
    publication = PagePublication.objects.get(page_id=selection.page_id)
    return {'pageId': str(selection.page_id), 'revision': selection.revision,
            'publicationRevision': publication.revision,
            'targetVersionId': str(publication.current_version_id) if publication.current_version_id else None}


@never_cache
@vary_on_cookie
def selection_view(request, page_id=None):
    bad = method(request, 'GET' if page_id is None else 'POST')
    if bad:
        return bad
    actor, bad = require_content(request, 'page', publish=page_id is not None)
    if bad:
        return bad
    if page_id is None:
        return response(request, selection_data(HomeSelection.objects.get(pk=1)))
    try:
        values = parse_json_object(request, max_bytes=2048, error_type=PageConfigError)
        if set(values) != {'expectedHomeRevision', 'expectedPublicationRevision'}:
            raise PageConfigError('设置首页参数不正确。')
        expected = values['expectedHomeRevision']
        if type(expected) is not int or expected < 1:
            raise PageConfigError('expectedHomeRevision 必须是正整数。')
        epoch, key = publication_revision(values), idempotency_key(request)
        digest = hashlib.sha256(json.dumps({'pageId': str(page_id), **values}, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        from .views import _lock_publication_graph
        with transaction.atomic():
            _lock_publication_graph()
            selection = HomeSelection.objects.select_for_update().get(pk=1)
            actor, bad = require_content(request, 'page', live=True, publish=True)
            if bad:
                return bad
            prior = HomeSelectionRequest.objects.filter(actor=actor, key=key).first()
            if prior:
                if prior.request_digest != digest:
                    return error(request, 409, 'IDEMPOTENCY_CONFLICT', '此幂等键已用于其他首页设置。')
                return response(request, prior.result)
            if selection.revision != expected:
                return error(request, 409, 'HOME_REVISION_CONFLICT', '首页已变化，请刷新后重试。')
            page = MicroPage.objects.select_for_update().filter(pk=page_id).first()
            if page is None:
                return error(request, 404, 'NOT_FOUND', '页面不存在。')
            publication = PagePublication.objects.select_for_update(of=('self',)).select_related('current_version').get(page=page)
            if publication.revision != epoch:
                return error(request, 409, 'PUBLICATION_REVISION_CONFLICT', '页面线上版本已变化，请刷新。')
            version = publication.current_version
            if version is None:
                return error(request, 422, 'PAGE_UNPUBLISHED', '请先发布页面，再设置为首页。')
            # Validate the published snapshot, never the potentially newer draft.
            try:
                validate_config(version.config_json, publishing=True, page_type=page.page_type, page_id=page.id)
                check_runtime(version.config_json)
            except (PageConfigError, CatalogError) as exc:
                if exc.status == 400:
                    raise PageConfigError(str(exc), "HOME_TARGET_INVALID", 422) from exc
                raise
            bad = confirm_action(request, actor, 'page.set_home', page.id, selection.revision)
            if bad:
                return bad
            before = {'pageId': str(selection.page_id), 'revision': selection.revision}
            selection.page = page
            selection.revision += 1
            selection.save(update_fields=['page', 'revision', 'updated_at'])
            result = selection_data(selection)
            HomeSelectionRequest.objects.create(actor=actor, key=key, request_digest=digest,
                page=page, version=version, result=result)
            audit(request, 'page.set_home', 'home_selection', 1, actor, before=before, after=result)
        return response(request, result)
    except (PageConfigError, CatalogError) as exc:
        return error(request, exc.status, exc.code, str(exc))
