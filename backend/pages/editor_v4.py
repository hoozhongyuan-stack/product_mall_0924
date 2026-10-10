"""Strict coupon references; entitlement logic belongs to benefits."""
from .validation import fields, fail, identifier
from benefits.page_coupons import public_campaign_exists, page_coupon_cards


def validate_coupons(props, *, publishing):
    fields(props, {'source', 'campaignIds', 'limit', 'layout'}, label='优惠券组件')
    if props['source'] not in ('MANUAL', 'AUTO') or props['layout'] not in ('LIST', 'SCROLL'):
        fail('优惠券来源或布局不支持。')
    if type(props['limit']) is not int or not 1 <= props['limit'] <= 10:
        fail('优惠券数量须为 1—10。')
    ids = props['campaignIds']
    if not isinstance(ids, list) or len(ids) > 10:
        fail('指定优惠券数量不正确。')
    parsed = [identifier(value, '优惠券活动 ID') for value in ids]
    if len(set(parsed)) != len(parsed):
        fail('优惠券活动不得重复。')
    if publishing and props['source'] == 'MANUAL' and (not ids or any(not public_campaign_exists(value) for value in ids)):
        fail('指定优惠券活动不存在或尚未公开。', publishing=True)


def coupon_component_data(config, member=None):
    return {item['componentId']: page_coupon_cards(item['props'], member) for item in config['components']
            if item['visible'] and item['type'] == 'COUPON_LIST'}
