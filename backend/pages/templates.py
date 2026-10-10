"""Built-in editable recipes; each caller receives independent draft objects."""


def component(identifier, kind, props, order):
    return {'componentId': identifier, 'type': kind, 'sortOrder': order, 'visible': True, 'props': props}


def title(text):
    return ('TITLE', {'text': text, 'subtitle': '', 'align': 'LEFT', 'size': 24})


def image():
    return ('IMAGE', {'assetId': '', 'ratio': '16:9'})


def navigation():
    return ('NAVIGATION', {'columns': 4, 'items': []})


def products(source):
    return ('PRODUCT_LIST', {'source': source, 'productIds': [], 'categoryId': '',
                             'limit': 8, 'layout': 'GRID', 'sort': 'NEWEST'})


def mosaic():
    return ('MOSAIC', {'template': 'FEATURED', 'items': [{'assetId': ''} for _ in range(3)], 'gap': 8})


def components(prefix, recipes):
    return [component(f'{prefix}_{index}', kind, props, index * 10) for index, (kind, props) in enumerate(recipes)]


def default_config(items):
    return {'schemaVersion': 3, 'pageType': 'MICRO', 'theme': {'pageBackgroundColor': '#F4F5F5',
            'headerBackgroundColor': '#FFFFFF', 'brandTextColor': '#A3202B'}, 'components': items,
            'metadata': {'tags': [], 'share': {'title': '', 'description': '', 'coverAssetId': ''}}}


def template_catalog():
    templates = [
        ('BRAND_HOME', '品牌首页', '补充品牌图片、分类入口和手选商品，搭建店铺首页。',
         [title('品牌精选'), image(), navigation(), products('MANUAL')]),
        ('CATEGORY_GUIDE', '分类导购', '配置分类导航和商品分类，帮助顾客查找商品。',
         [title('发现好物'), ('SEARCH', {'placeholder': '搜索商品'}), navigation(), products('CATEGORY')]),
        ('CAMPAIGN', '活动页面', '补充活动图片、说明和手选商品，搭建专题页面。',
         [title('活动精选'), mosaic(), ('NOTICE', {'text': '请填写活动说明'}), products('MANUAL')])]
    combinations = [
        ('BRAND_HEADER', '品牌页头', '标题、图片广告和图文导航。', [title('品牌精选'), image(), navigation()]),
        ('CATEGORY_SECTION', '分类商品分组', '标题和分类商品展示。', [title('分类精选'), products('CATEGORY')]),
        ('CAMPAIGN_ENTRY', '活动入口', '标题和重点活动魔方。', [title('精彩活动'), mosaic()])]
    return {'templates': [{'templateId': key, 'version': 1, 'name': name, 'description': description,
             'config': default_config(components(key, recipes))} for key, name, description, recipes in templates],
            'combinations': [{'combinationId': key, 'version': 1, 'name': name, 'description': description,
             'components': components(key, recipes)} for key, name, description, recipes in combinations]}
