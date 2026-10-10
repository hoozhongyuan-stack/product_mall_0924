"""Read-only public coupon cards, sharing the existing distribution rules."""
from django.db.models import Case, When, IntegerField, F
from django.utils import timezone
from catalog.coupon_targets import page_coupon_product_names
from .models import CouponCampaign, CouponAllocation


def public_campaign_exists(identifier):
    return CouponCampaign.objects.filter(id=identifier, status='PUBLISHED', claim_mode__in=['SELF', 'BOTH']).exists()


def claim_state(row, member, count, at):
    if not row.active or not row.issuance_enabled:
        return 'UNAVAILABLE'
    if at >= row.valid_until:
        return 'EXPIRED'
    if at < row.valid_from:
        return 'NOT_STARTED'
    if row.issued_quantity >= row.total_quantity:
        return 'SOLD_OUT'
    if member is None:
        return 'GUEST'
    return 'LIMIT_REACHED' if count >= row.self_claim_limit else 'AVAILABLE'


def page_coupon_cards(props, member=None):
    at = timezone.now()
    query = CouponCampaign.objects.filter(status='PUBLISHED', claim_mode__in=['SELF', 'BOTH'])
    if props['source'] == 'MANUAL':
        if not props['campaignIds']:
            return []
        query = query.filter(id__in=props['campaignIds']).order_by(Case(
            *[When(id=identifier, then=index) for index, identifier in enumerate(props['campaignIds'])],
            output_field=IntegerField()))
    else:
        query = query.filter(active=True, issuance_enabled=True, valid_from__lte=at,
                             valid_until__gt=at, issued_quantity__lt=F('total_quantity')).order_by('valid_until', 'id')
    rows = list(query[:props['limit']])
    counts = dict(CouponAllocation.objects.filter(member=member, campaign_id__in=[row.id for row in rows])
                  .values_list('campaign_id', 'self_count')) if member else {}
    names = {item['id']: item for item in page_coupon_product_names({value for row in rows for value in row.product_ids})}
    cards = []
    for row in rows:
        count = counts.get(row.id, 0)
        state = claim_state(row, member, count, at)
        public_ids = [value for value in row.product_ids if value in names]
        cards.append({'id': str(row.id), 'title': row.title, 'kind': row.kind, 'minGoodsFen': row.min_goods_fen,
            'discountFen': row.discount_fen, 'productIds': public_ids, 'productNames': [names[value] for value in public_ids],
            'scopeRestricted': bool(row.product_ids), 'scopeLabel': '指定商品' if row.product_ids else '全部商品',
            'redeemEligible': row.redeem_eligible, 'validFrom': row.valid_from.isoformat(),
            'validUntil': row.valid_until.isoformat(), 'remainingQuantity': row.total_quantity - row.issued_quantity,
            'selfClaimLimit': row.self_claim_limit, 'selfClaimedCount': count, 'canClaim': state == 'AVAILABLE', 'claimState': state})
    return cards


def campaign_states(identifiers):
    at = timezone.now()
    return {str(row.id): claim_state(row, None, 0, at) for row in
            CouponCampaign.objects.filter(id__in=identifiers, status='PUBLISHED', claim_mode__in=['SELF', 'BOTH'])}
