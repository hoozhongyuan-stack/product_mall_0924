"""Fixed decoration layouts and publication-owned sharing metadata."""
from .validation import fields, fail, string, asset_id, check_link

SLOTS = {'TWO': 2, 'THREE': 3, 'FOUR': 4, 'FEATURED': 3}


def validate_layout(component, *, publishing, page_id, graph_cache):
    props = component['props']
    if component['type'] == 'SPACER':
        fields(props, {'height'}, label='辅助空白')
        if type(props['height']) is not int or props['height'] not in (4, 8, 12, 16, 24, 32, 48, 64, 96):
            fail('辅助空白高度不支持。')
        return
    fields(props, {'template', 'items', 'gap'}, label='魔方')
    if not isinstance(props['template'], str) or props['template'] not in SLOTS:
        fail('魔方模板不支持。')
    if type(props['gap']) is not int or props['gap'] not in (0, 4, 8, 12, 16):
        fail('魔方间距不支持。')
    if not isinstance(props['items'], list) or len(props['items']) != SLOTS[props['template']]:
        fail('魔方图片数量与模板不匹配。', publishing=publishing)
    for item in props['items']:
        fields(item, {'assetId'}, {'title', 'link'}, label='魔方图片')
        asset_id(item['assetId'], publishing=publishing)
        if 'title' in item:
            string(item['title'], '魔方图片标题', 40, nonempty=False)
        if item.get('link') is not None:
            check_link(item['link'], publishing=publishing, page_id=page_id, graph_cache=graph_cache)


def validate_metadata(value, *, publishing):
    fields(value, {'tags', 'share'}, label='页面运营资料')
    tags = value['tags']
    if not isinstance(tags, list) or len(tags) > 5:
        fail('页面标签最多 5 个。')
    for tag in tags:
        string(tag, '页面标签', 20)
        if tag != tag.strip():
            fail('页面标签不能包含首尾空白。')
    if len(set(tags)) != len(tags):
        fail('页面标签不得重复。')
    share = value['share']
    fields(share, {'title', 'description', 'coverAssetId'}, label='分享资料')
    string(share['title'], '分享标题', 60, nonempty=False)
    string(share['description'], '分享说明', 120, nonempty=False)
    string(share['coverAssetId'], '分享封面 ID', 36, nonempty=False)
    if share['coverAssetId']:
        asset_id(share['coverAssetId'], publishing=publishing)


def sharing(config):
    share = config.get('metadata', {}).get('share', {})
    identifier = share.get('coverAssetId')
    return {'title': share.get('title', ''), 'description': share.get('description', ''),
            'coverUrl': f'/api/v1/app/assets/{identifier}/file' if identifier else ''}
