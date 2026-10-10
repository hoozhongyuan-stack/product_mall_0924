"""Private, read-only publication review with independent reference diagnostics."""
from django.db import transaction
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie
from common.http import method
from accounts.security import require, response, error
from catalog.page_targets import page_image_is_available
from catalog.validation import CatalogError
from benefits.page_coupons import campaign_states
from .models import MicroPage, PagePublication
from .validation import PageConfigError, validate_config, validate_component, check_link
from .runtime import runtime_version
from .views import _body, _revision, _lock_publication_graph
from .history import publication_revision


def _components(config):
    values = config.get('components', []) if isinstance(config, dict) else []
    return [item for item in values if isinstance(item, dict) and isinstance(item.get('componentId'), str)] if isinstance(values, list) else []


def differences(draft, previous):
    draft = draft if isinstance(draft, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    before = {item['componentId']: item for item in _components(previous)}
    after = {item['componentId']: item for item in _components(draft)}
    return {'addedComponentIds': sorted(after.keys() - before.keys()), 'removedComponentIds': sorted(before.keys() - after.keys()),
            'updatedComponentIds': sorted(key for key in after.keys() & before.keys() if after[key] != before[key]),
            'orderChanged': [(item['componentId'], item.get('sortOrder')) for item in _components(draft)] !=
                            [(item['componentId'], item.get('sortOrder')) for item in _components(previous)],
            'themeChanged': draft.get('theme') != previous.get('theme'),
            'metadataChanged': draft.get('metadata') != previous.get('metadata')}


def link_slots(kind, props):
    if kind in ('IMAGE', 'NOTICE'):
        return [('link', props.get('link'))]
    key = {'CAROUSEL': 'slides', 'IMAGE_HOTZONE': 'areas', 'NAVIGATION': 'items', 'MOSAIC': 'items'}.get(kind)
    items = props.get(key, []) if key else []
    return [(f'{key}.{index}.link', item.get('link')) for index, item in enumerate(items)
            if isinstance(item, dict)] if isinstance(items, list) else []


def asset_slots(kind, props):
    if kind in ('IMAGE', 'IMAGE_HOTZONE'):
        return [('assetId', props.get('assetId'))]
    key = {'CAROUSEL': 'slides', 'NAVIGATION': 'items', 'MOSAIC': 'items'}.get(kind)
    items = props.get(key, []) if key else []
    return [(f'{key}.{index}.assetId', item.get('assetId')) for index, item in enumerate(items)
            if isinstance(item, dict)] if isinstance(items, list) else []


def collect_issues(page):
    config, issues, seen = page.draft_config, [], set()
    graph_cache = {}
    def add(path, code, message, component_id=None, severity='ERROR'):
        identity = (path, code, message)
        if identity not in seen:
            seen.add(identity)
            issues.append({'path': path, 'code': code, 'message': message, 'severity': severity,
                           **({'componentId': component_id} if component_id else {})})
    def inspect(path, action, component_id=None):
        try:
            action()
        except (PageConfigError, CatalogError) as exc:
            add(path, exc.code, str(exc), component_id)
    inspect('config', lambda: validate_config(config, page_type=page.page_type, page_id=page.id))
    components = config.get('components', []) if isinstance(config, dict) else []
    for index, component in enumerate(components if isinstance(components, list) else []):
        if not isinstance(component, dict):
            continue
        component_id = component.get('componentId') if isinstance(component.get('componentId'), str) else None
        path = f'components.{index}'
        visible = component.get('visible') is True
        inspect(path, lambda: validate_component(component, publishing=visible, page_id=page.id,
                graph_cache=graph_cache, schema_version=config.get('schemaVersion', 1) if type(config.get('schemaVersion')) is int else 1), component_id)
        props = component.get('props', {})
        if not isinstance(props, dict):
            continue
        kind = component.get('type')
        if not isinstance(kind, str):
            continue
        for slot, identifier in asset_slots(kind, props):
            if identifier and isinstance(identifier, str):
                inspect(f'{path}.props.{slot}', lambda value=identifier: _image(value), component_id)
        if not visible:
            continue
        for slot, link in link_slots(kind, props):
            if link is not None:
                inspect(f'{path}.props.{slot}', lambda value=link: check_link(value, publishing=True,
                        page_id=page.id, graph_cache=graph_cache), component_id)
        _business_refs(kind, props, path, component_id, add, inspect)
    metadata = config.get('metadata', {}) if isinstance(config, dict) else {}
    share = metadata.get('share', {}) if isinstance(metadata, dict) else {}
    cover = share.get('coverAssetId') if isinstance(share, dict) else None
    if cover and isinstance(cover, str):
        inspect('metadata.share.coverAssetId', lambda: _image(cover))
    schema = config.get('schemaVersion') if isinstance(config, dict) else None
    if type(schema) is not int or schema not in (1, 2, 3, 4) or schema > runtime_version():
        add('schemaVersion', 'PAGE_RUNTIME_UNSUPPORTED', '当前已验证的小程序版本不支持此页面配置。')
    return issues


def _image(value):
    from .validation import identifier
    if not page_image_is_available(identifier(value, '素材 ID')):
        raise PageConfigError('图片素材不可用或文件已丢失。', 'MEDIA_INVALID', 422)


def _business_refs(kind, props, path, component_id, add, inspect):
    if kind == 'PRODUCT_LIST':
        if props.get('source') == 'MANUAL' and isinstance(props.get('productIds'), list):
            for index, identifier in enumerate(props['productIds']):
                inspect(f'{path}.props.productIds.{index}', lambda value=identifier: _available(value, 'PRODUCT'), component_id)
        elif props.get('source') == 'CATEGORY' and props.get('categoryId'):
            inspect(f'{path}.props.categoryId', lambda: _available(props['categoryId'], 'CATEGORY'), component_id)
    if kind == 'COUPON_LIST' and props.get('source') == 'MANUAL' and isinstance(props.get('campaignIds'), list):
        from .validation import identifier
        valid_ids = []
        for index, value in enumerate(props['campaignIds']):
            inspect(f'{path}.props.campaignIds.{index}', lambda value=value: identifier(value, '活动 ID'), component_id)
            try:
                valid_ids.append(str(identifier(value, '活动 ID')))
            except PageConfigError:
                continue
        states = campaign_states(valid_ids)
        for index, value in enumerate(props['campaignIds']):
            try:
                state = states.get(str(identifier(value, '活动 ID')))
            except PageConfigError:
                state = None
            if state is None:
                add(f'{path}.props.campaignIds.{index}', 'COUPON_REFERENCE_INVALID', '活动不存在、未公开或不支持自行领取。', component_id)
            elif state not in ('GUEST', 'AVAILABLE'):
                add(f'{path}.props.campaignIds.{index}', 'COUPON_' + state, {'SOLD_OUT': '活动优惠券已领完。', 'EXPIRED': '活动优惠券已过期。',
                     'NOT_STARTED': '活动优惠券尚未开始领取。', 'UNAVAILABLE': '活动优惠券当前已暂停领取。',
                     'LIMIT_REACHED': '当前会员已达到领取上限。'}.get(state, '活动优惠券当前不可领取。'), component_id, 'WARNING')


def _available(value, kind):
    check_link({'type': kind, 'targetId': value}, publishing=True)


@never_cache
@vary_on_cookie
def release_report_view(request, page_id=None):
    bad = method(request, 'POST')
    if bad:
        return bad
    _, bad = require(request, 'page.read')
    if bad:
        return bad
    try:
        values = _body(request)
        if set(values) != {'expectedRevision', 'expectedPublicationRevision'}:
            raise PageConfigError('发布报告参数不正确。')
        revision, epoch = _revision(values), publication_revision(values)
        with transaction.atomic():
            _lock_publication_graph()
            query = MicroPage.objects.select_for_update()
            page = query.filter(pk=page_id, page_type='MICRO').first() if page_id else query.filter(page_type='HOME').first()
            if not page:
                return error(request, 404, 'NOT_FOUND', '页面不存在。')
            publication = PagePublication.objects.select_for_update().get(page=page)
            if page.draft_revision != revision:
                return error(request, 409, 'REVISION_CONFLICT', '草稿已修改，请刷新。')
            if publication.revision != epoch:
                return error(request, 409, 'PUBLICATION_REVISION_CONFLICT', '线上版本已变化，请刷新。')
            version = publication.current_version
            issues = collect_issues(page)
            schema = page.draft_config.get('schemaVersion') if isinstance(page.draft_config, dict) else None
            return response(request, {'pageId': str(page.id), 'revision': revision, 'publicationRevision': epoch,
                'publishedVersionId': str(version.id) if version else None, 'schemaVersion': schema,
                'runtimeSchemaVersion': runtime_version(), 'runtimeSupported': type(schema) is int and schema in (1, 2, 3, 4) and schema <= runtime_version(),
                'diff': {**differences(page.draft_config, version.config_json if version else {}),
                         'nameChanged': version is None or page.name != version.name}, 'issues': issues,
                'canPublish': not any(item['severity'] == 'ERROR' for item in issues)})
    except (PageConfigError, CatalogError) as exc:
        return error(request, exc.status, exc.code, str(exc))
