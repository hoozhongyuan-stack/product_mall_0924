"""Synthetic visual data, executed only inside run-browser-integration.py --preview.

Uses existing authenticated application APIs; never seeds an existing business DB.
The committed startup fallback is an explicitly labelled placeholder, not product art.
"""
import json
import os
from datetime import date, timedelta
import re
import uuid

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from catalog.models import Product
from orders.models import Order


def seed():
    database = settings.DATABASES['default']
    if (os.environ.get('MALL_E2E_PREVIEW') != '1'
            or not re.fullmatch(r'test_health_e2e_[0-9a-f]{16}', database['NAME'])
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise RuntimeError('Visual fixtures require a newly owned local preview database.')
    if (any(settings.ORDER_PAYMENT_METHODS_ENABLED.values()) or settings.EXCHANGE_ORDER_ENABLED
            or settings.WECHAT_REFUND_ENABLED or settings.WECHAT_MINI_APP_ID
            or settings.WECHAT_MINI_APP_SECRET or settings.KDNIAO['ENABLED']):
        raise RuntimeError('Preview must not enable orders or external platform credentials.')
    if Product.objects.exists() or Order.objects.exists():
        raise RuntimeError('Refusing to seed a database containing business data.')
    client = Client(enforce_csrf_checks=True, HTTP_HOST='127.0.0.1')
    client.get('/api/v1/admin/auth/csrf')

    def send(method, path, body, key=None, confirmation=None):
        headers = {'HTTP_X_CSRFTOKEN': client.cookies['csrftoken'].value}
        if key:
            headers['HTTP_IDEMPOTENCY_KEY'] = key
        if confirmation:
            headers['HTTP_X_ACTION_CONFIRMATION'] = confirmation
        response = getattr(client, method)(path, data=json.dumps(body),
                                          content_type='application/json', **headers)
        payload = response.json()
        if response.status_code not in (200, 201) or not payload.get('success'):
            # No request bodies/credentials in diagnostics.
            raise RuntimeError(f'Preview seed {method} {path}: {response.status_code} '
                               f'{payload.get("error", {}).get("code", "UNKNOWN")}')
        return payload['data']

    send('post', '/api/v1/admin/auth/login', {
        'loginName': os.environ['MALL_E2E_OWNER'],
        'password': os.environ['MALL_E2E_OWNER_PASSWORD'],
    })
    root = send('post', '/api/v1/admin/categories', {
        'parentId': None, 'name': '验收示例', 'sortOrder': 1, 'status': 'ACTIVE',
    })
    leaf = send('post', '/api/v1/admin/categories', {
        'parentId': root['id'], 'name': '商品与服务', 'sortOrder': 1, 'status': 'ACTIVE',
    })
    image_path = settings.BASE_DIR.parent / 'mini-program/assets/startup-brand.png'
    response = client.post('/api/v1/admin/assets', {
        'kind': 'IMAGE',
        'file': SimpleUploadedFile('验收占位图.png', image_path.read_bytes(), content_type='image/png'),
    }, HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
    if response.status_code != 201:
        raise RuntimeError(f'Preview placeholder upload failed: {response.status_code}')
    asset_id = response.json()['data']['assetId']
    warehouse = send('post', '/api/v1/admin/warehouses', {
        'code': 'PREVIEW', 'name': '验收示例仓', 'isDefault': True,
    })

    def product(number, name, fulfillment, labels, *, publish):
        values = [(f'pack-{index}', label, price, stock)
                  for index, (label, price, stock) in enumerate(labels)]
        payload = {
            'productNo': number, 'name': name, 'categoryId': leaf['id'],
            'fulfillmentKind': fulfillment, 'status': 'DRAFT',
            'descriptionHtml': '<p>本机视觉验收使用的合成商品，不用于真实销售。图片为验收占位素材。</p>',
            'mainImageAssetId': asset_id, 'galleryAssetIds': [],
            'specAxes': [{'clientKey': 'pack', 'name': '包装规格', 'sortOrder': 0,
                          'options': [{'clientKey': key, 'value': label, 'sortOrder': index}
                                      for index, (key, label, _, _) in enumerate(values)]}],
            'skus': [{'skuCode': f'{number}-{index + 1}', 'specOptionKeys': [key],
                      'listPriceFen': price, 'saleStatus': 'OFF_SALE', 'gradePrices': [],
                      'unit': {'baseUnit': '件', 'saleUnit': '件', 'ratio': 1}}
                     for index, (key, _, price, _) in enumerate(values)],
        }
        if fulfillment == 'REDEEM':
            payload['redeemValidUntil'] = (date.today() + timedelta(days=90)).isoformat()
        created = send('post', '/api/v1/admin/products', payload)
        stock_rows = [{'skuId': row['skuId'], 'quantity': value[3], 'unit': 'BASE'}
                      for row, value in zip(created['skus'], values) if value[3] > 0]
        if stock_rows:
            inbound = send('post', '/api/v1/admin/inventory/inbounds', {
                'warehouseId': warehouse['warehouseId'], 'reason': '合成验收库存', 'items': stock_rows,
            }, str(uuid.uuid4()))
            send('post', f'/api/v1/admin/inventory/inbounds/{inbound["inboundId"]}/confirm',
                 {'expectedRevision': inbound['revision']}, str(uuid.uuid4()))
        if publish:
            for sku in created['skus']:
                send('patch', f'/api/v1/admin/skus/{sku["skuId"]}/status', {
                    'saleStatus': 'ON_SALE', 'expectedRevision': sku['skuRevision'],
                })
        return created['productId']

    wine_id = product('PREVIEW-WINE', '验收示例 · 经典干红 750ml', 'SHIP', [
        ('单瓶装', 26800, 12), ('双支礼盒', 49800, 3), ('整箱装 · 缺货示例', 148800, 0),
    ], publish=True)
    product('PREVIEW-SERVICE', '验收示例 · 到店品鉴服务', 'REDEEM', [
        ('单人体验', 12800, 8),
    ], publish=True)
    product('PREVIEW-DRAFT', '验收示例 · 多规格草稿（可编辑）', 'SHIP', [
        ('标准装', 8800, 0), ('礼盒装', 16800, 0),
    ], publish=False)

    def publish_page(path, draft):
        confirmation = send('post', '/api/v1/admin/auth/confirm', {
            'action': 'page.publish', 'password': os.environ['MALL_E2E_OWNER_PASSWORD'],
            'objectId': draft['pageId'], 'revision': draft['revision'],
        })
        send('post', f'{path}/publish', {
            'expectedRevision': draft['revision'],
            'expectedPublicationRevision': draft['publicationRevision'],
        }, str(uuid.uuid4()), confirmation['confirmationToken'])

    micro = send('post', '/api/v1/admin/pages', {'name': '验收示例 · 到店须知'})
    micro_path = f'/api/v1/admin/pages/{micro["pageId"]}'
    micro = send('put', f'{micro_path}/draft', {
        'name': micro['name'], 'expectedRevision': micro['revision'],
        'config': {**micro['config'], 'components': [{
            'componentId': 'preview-notice', 'type': 'NOTICE', 'sortOrder': 10, 'visible': True,
            'props': {'text': '本页为本机合成验收内容。商品、库存和图片均为示例。',
                      'link': {'type': 'FUNCTION', 'targetId': 'CATALOG'}},
        }]},
    })
    publish_page(micro_path, micro)
    home_path = '/api/v1/admin/pages/home'
    home = client.get(f'{home_path}/draft').json()['data']
    home = send('put', f'{home_path}/draft', {
        'expectedRevision': home['revision'],
        'config': {**home['config'], 'components': [
            {'componentId': 'preview-search', 'type': 'SEARCH', 'sortOrder': 10, 'visible': True,
             'props': {'placeholder': '搜索商品与服务'}},
            {'componentId': 'preview-notice', 'type': 'NOTICE', 'sortOrder': 20, 'visible': True,
             'props': {'text': '本机视觉验收 · 查看到店须知',
                       'link': {'type': 'PAGE', 'targetId': micro['pageId']}}},
            {'componentId': 'preview-hero', 'type': 'CAROUSEL', 'sortOrder': 30, 'visible': True,
             'props': {'slides': [{'assetId': asset_id,
                                  'link': {'type': 'PRODUCT', 'targetId': wine_id}}]}},
        ]},
    })
    publish_page(home_path, home)
    for path in ('/api/v1/app/home', f'/api/v1/app/pages/{micro["pageId"]}'):
        if Client(HTTP_HOST='127.0.0.1').get(path).status_code != 200:
            raise RuntimeError('Published synthetic content is not publicly readable.')
    send('post', '/api/v1/admin/auth/logout', {})
    if Order.objects.exists():
        raise RuntimeError('Preview seed unexpectedly created orders.')
    print('Synthetic preview ready: 3 products / 6 SKUs / home and micro page; payment and exchange submission remain closed.')


seed()
