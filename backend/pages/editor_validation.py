"""Schema 2 decoration components; business facts remain in catalog."""
import re
from .validation import fields, fail, string, identifier, asset_id, check_link


def appearance(value):
    fields(value, set(), {'backgroundColor', 'padding', 'margin', 'radius'}, label='外观')
    if 'backgroundColor' in value and (not isinstance(value['backgroundColor'], str) or
            not re.fullmatch(r'#[0-9a-fA-F]{6}', value['backgroundColor'])):
        fail('背景色须使用 #RRGGBB。')
    for key, allowed in [('padding', {0, 4, 8, 12, 16, 24, 32}),
                         ('margin', {0, 4, 8, 12, 16, 24, 32}), ('radius', {0, 8, 16})]:
        if key in value and (type(value[key]) is not int or value[key] not in allowed):
            fail('外观尺寸不支持。')


def validate_new(component, *, publishing, page_id, graph_cache):
    kind, props = component['type'], component['props']
    def link(value):
        check_link(value, publishing=publishing, page_id=page_id, graph_cache=graph_cache)
    if kind == 'TITLE':
        fields(props, {'text', 'align', 'size'}, {'subtitle'}, label='标题')
        string(props['text'], '标题', 100, nonempty=publishing)
        if 'subtitle' in props:
            string(props['subtitle'], '副标题', 200, nonempty=False)
        if props['align'] not in ('LEFT', 'CENTER') or type(props['size']) is not int or props['size'] not in (16, 20, 24):
            fail('标题样式不支持。')
    elif kind == 'IMAGE':
        fields(props, {'assetId', 'ratio'}, {'link'}, label='图片广告')
        asset_id(props['assetId'], publishing=publishing)
        if props['ratio'] not in ('AUTO', '1:1', '16:9'):
            fail('图片比例不支持。')
        if props.get('link') is not None:
            link(props['link'])
    elif kind == 'NAVIGATION':
        fields(props, {'items', 'columns'}, label='图文导航')
        if type(props['columns']) is not int or props['columns'] not in (2, 3, 4):
            fail('导航列数不支持。')
        if not isinstance(props['items'], list) or len(props['items']) > 20 or (publishing and not props['items']):
            fail('导航数量不正确。', publishing=publishing)
        for item in props['items']:
            fields(item, {'title', 'link'}, {'assetId'}, label='导航项')
            string(item['title'], '导航标题', 40, nonempty=publishing)
            if item.get('assetId'):
                asset_id(item['assetId'], publishing=publishing)
            link(item['link'])
    elif kind == 'PRODUCT_LIST':
        fields(props, {'source', 'productIds', 'categoryId', 'limit', 'layout', 'sort'}, label='商品组件')
        if props['source'] not in ('MANUAL', 'CATEGORY') or props['layout'] not in ('GRID', 'LIST', 'SCROLL') or props['sort'] not in ('NEWEST', 'PRICE_ASC'):
            fail('商品组件配置不支持。')
        if type(props['limit']) is not int or not 1 <= props['limit'] <= 20:
            fail('商品数量须为 1—20。')
        ids = props['productIds']
        if not isinstance(ids, list) or len(ids) > 20 or len(set(str(i) for i in ids)) != len(ids):
            fail('指定商品数量或 ID 不正确。')
        for value in ids:
            identifier(value, '商品 ID')
        string(props['categoryId'], '分类 ID', 36, nonempty=False)
        if props['categoryId']:
            identifier(props['categoryId'], '分类 ID')
        if publishing:
            from catalog.page_targets import product_is_available, category_is_available
            if props['source'] == 'MANUAL' and (not ids or any(not product_is_available(i) for i in ids)):
                fail('指定商品不可用或尚未上架。', publishing=True)
            if props['source'] == 'CATEGORY' and (not props['categoryId'] or not category_is_available(props['categoryId'])):
                fail('商品分类不可用。', publishing=True)
    else:
        fail('组件类型不支持。')
