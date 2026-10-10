"""Deployment fence and transient catalog data for versioned pages."""
from django.conf import settings
from catalog.page_products import hydrate_products
from .validation import PageConfigError


def runtime_version():
    value = getattr(settings, 'PAGE_RUNTIME_SCHEMA_VERSION', 1)
    return value if type(value) is int and value in (1, 2, 3, 4) else 1


def check_runtime(config):
    if config['schemaVersion'] > runtime_version():
        raise PageConfigError('当前小程序尚未验证支持此页面版本。', 'PAGE_RUNTIME_UNSUPPORTED', 422)


def public_config(request, config):
    value = request.GET.get('schemaVersion', '1')
    if value not in ('1', '2', '3', '4') or len(request.GET.getlist('schemaVersion')) > 1:
        raise PageConfigError('客户端页面版本不正确。')
    if config['schemaVersion'] > int(value):
        raise PageConfigError('请更新小程序以查看此页面。', 'PAGE_SCHEMA_UNSUPPORTED', 422)
    public = {key: value for key, value in config.items() if key != 'metadata'}
    return {**public, 'components': [_public_component(item) for item in config['components'] if item['visible']]}


def component_data(config):
    return {item['componentId']: hydrate_products(item['props']) for item in config['components']
            if item['visible'] and item['type'] == 'PRODUCT_LIST'}


def _public_component(item):
    props = item['props']
    if item['type'] == 'COUPON_LIST' and props['source'] == 'AUTO':
        return {**item, 'props': {**props, 'campaignIds': []}}
    if item['type'] == 'PRODUCT_LIST':
        ignored = {'productIds': []} if props['source'] == 'CATEGORY' else {'categoryId': ''}
        return {**item, 'props': {**props, **ignored}}
    return item
