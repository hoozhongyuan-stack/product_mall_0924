"""Operator capability, product preview and idempotent independent drafts."""
from copy import deepcopy
import hashlib
import json
import uuid
from django.db import transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie
from common.http import method
from accounts.security import require, require_live, response, error, audit
from catalog.asset_access import authorize_asset_binding
from catalog.page_targets import assets_exist
from catalog.page_products import hydrate_products
from catalog.validation import CatalogError
from .validation import PageConfigError, validate_config, referenced_assets, validate_component
from .runtime import runtime_version
from .models import MicroPage, PagePublication, PageDraftAsset, PageCopyRequest
from .views import _body, _name, _revision, _draft_data
from .history import idempotency_key


@never_cache
@vary_on_cookie
def capabilities_view(request):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, bad = require(request, 'page.read')
    return bad or response(request, {'runtimeSchemaVersion': runtime_version(), 'supportedSchemaVersions': [1, 2, 3, 4]})


@never_cache
@vary_on_cookie
def product_preview_view(request):
    bad = method(request, 'POST')
    if bad:
        return bad
    _, bad = require(request, 'page.read')
    if bad:
        return bad
    try:
        values = _body(request)
        if set(values) != {'props'}:
            raise PageConfigError('商品预览参数不正确。')
        validate_component({'componentId': 'preview', 'type': 'PRODUCT_LIST', 'sortOrder': 0,
                            'visible': True, 'props': values['props']}, publishing=False, schema_version=2)
        return response(request, {'products': hydrate_products(values['props'])})
    except (PageConfigError, CatalogError) as exc:
        return error(request, exc.status, exc.code, str(exc))


@never_cache
@vary_on_cookie
def copy_view(request, page_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    actor, bad = require(request, 'page.edit')
    if bad:
        return bad
    try:
        values = _body(request)
        if set(values) != {'name', 'source', 'expectedRevision'} or values['source'] not in ('DRAFT', 'PUBLISHED'):
            raise PageConfigError('复制页面参数不正确。')
        name, revision, key = _name(values['name']), _revision(values), idempotency_key(request)
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with transaction.atomic():
            source = MicroPage.objects.select_for_update().filter(pk=page_id).first()
            if source is None:
                return error(request, 404, 'NOT_FOUND', '页面不存在。')
            actor, bad = require_live(request, 'page.edit')
            if bad:
                return bad
            _, bad = require_live(request, 'page.read')
            if bad:
                return bad
            prior = PageCopyRequest.objects.filter(actor=actor, source_page=source, key=key).first()
            if prior:
                if prior.request_digest != digest:
                    return error(request, 409, 'IDEMPOTENCY_CONFLICT', '此幂等键已用于其他复制请求。')
                return response(request, prior.result, 201)
            publication = PagePublication.objects.select_for_update().get(page=source)
            version = publication.current_version
            selected_revision = source.draft_revision if values['source'] == 'DRAFT' else (version.revision if version else None)
            if selected_revision != revision:
                return error(request, 409, 'REVISION_CONFLICT', '源页面版本已变化或尚未发布，请刷新。')
            config = deepcopy(source.draft_config if values['source'] == 'DRAFT' else version.config_json)
            config['pageType'] = 'MICRO'
            config['components'] = [{**item, 'componentId': uuid.uuid4().hex} for item in config['components']]
            validate_config(config, page_type='MICRO')
            identifiers = referenced_assets(config)
            if not assets_exist(identifiers):
                raise PageConfigError('源页面素材不可用。')
            authorize_asset_binding(actor, identifiers, [])
            target = MicroPage.objects.create(page_type='MICRO', name=name, draft_config=config)
            PagePublication.objects.create(page=target)
            PageDraftAsset.objects.bulk_create([PageDraftAsset(page=target, asset_id=value) for value in identifiers])
            result = _draft_data(target)
            PageCopyRequest.objects.create(actor=actor, source_page=source, key=key, request_digest=digest, result=result)
            audit(request, 'page.copy', 'micro_page', target.id, actor,
                  after={'sourcePageId': str(source.id), 'source': values['source'], 'name': name})
        return response(request, result, 201)
    except (PageConfigError, CatalogError) as exc:
        return error(request, exc.status, exc.code, str(exc))


@never_cache
@vary_on_cookie
def templates_view(request):
    bad = method(request, 'GET')
    if bad:
        return bad
    _, bad = require(request, 'page.read')
    if bad:
        return bad
    from .templates import template_catalog
    return response(request, template_catalog())
