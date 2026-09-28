"""Coupon distribution boundary. Lock order: member then campaign then allocation.

Campaign publishing never touches member rows. Distribution consumes a permanent
quota, independent of redemption, cancellation, and refund restoration.
"""
import hashlib
import json
import re
from uuid import UUID, uuid4
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from catalog.coupon_targets import coupon_product_names, coupon_products_exist
from customers.consumption import lock_consumption_member
from .models import CouponCampaign, CouponAllocation, CouponIssuance, MemberCoupon
from .service import BenefitError, MAX_AMOUNT_FEN, _uuid

EDITABLE = {'code','title','kind','minGoodsFen','discountFen','productIds','redeemEligible',
            'validFrom','validUntil','totalQuantity','selfClaimLimit','claimMode','issuanceEnabled'}


def fingerprint(action, campaign_id, body):
    value={'action':action,'campaignId':str(campaign_id) if campaign_id else None,'body':body}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def revision_value(value):
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise BenefitError('修订号格式不正确。','VALIDATION_FAILED')
    return value


def fields(body):
    if not isinstance(body,dict) or set(body) != EDITABLE:
        raise BenefitError('请填写完整活动内容，且不要增加不支持的字段。','VALIDATION_FAILED')
    for name,limit in [('code',64),('title',100)]:
        if not isinstance(body[name],str) or not 1 <= len(body[name].strip()) <= limit:
            raise BenefitError('活动名称或编号格式不正确。','VALIDATION_FAILED')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',body['code'].strip()):
        raise BenefitError('活动编号只能包含 ASCII 字母、数字、下划线和短横线。','VALIDATION_FAILED')
    if body['kind'] not in CouponCampaign.Kind.values or body['claimMode'] not in CouponCampaign.ClaimMode.values:
        raise BenefitError('券类型或领取方式不正确。','VALIDATION_FAILED')
    for key,low,high in [('minGoodsFen',0,MAX_AMOUNT_FEN),('discountFen',1,MAX_AMOUNT_FEN),
                         ('totalQuantity',1,1_000_000),('selfClaimLimit',1,100)]:
        if type(body[key]) is not int or not low <= body[key] <= high:
            raise BenefitError('金额、总量或领取上限须为范围内的整数。','VALIDATION_FAILED')
    if (body['kind']=='CASH' and body['minGoodsFen'] != 0) or (body['kind']=='FULL_REDUCTION' and body['minGoodsFen'] <= 0):
        raise BenefitError('现金券无门槛，满减券须填写正数门槛。','VALIDATION_FAILED')
    if body['selfClaimLimit'] > min(body['totalQuantity'],100):
        raise BenefitError('每人领取上限不能超过活动总量。','VALIDATION_FAILED')
    ids=body['productIds']
    if not isinstance(ids,list) or len(ids)>100:
        raise BenefitError('最多指定 100 个商品。','VALIDATION_FAILED')
    ids=[str(_uuid(value,'商品')) for value in ids]
    if len(set(ids)) != len(ids) or not coupon_products_exist(ids):
        raise BenefitError('指定商品重复或不存在。','VALIDATION_FAILED')
    if type(body['redeemEligible']) is not bool or type(body['issuanceEnabled']) is not bool:
        raise BenefitError('活动开关须为布尔值。','VALIDATION_FAILED')
    dates=[]
    for key in ['validFrom','validUntil']:
        value=body[key]
        try: date=parse_datetime(value) if isinstance(value,str) and len(value)<=40 else None
        except ValueError: date=None
        if date is None or not timezone.is_aware(date):
            raise BenefitError('有效期须包含时区。','VALIDATION_FAILED')
        dates.append(date)
    if dates[1] <= dates[0]:
        raise BenefitError('截止时间须晚于开始时间。','VALIDATION_FAILED')
    return dict(code=body['code'].strip(),title=body['title'].strip(),kind=body['kind'],
        min_goods_fen=body['minGoodsFen'],discount_fen=body['discountFen'],product_ids=ids,
        redeem_eligible=body['redeemEligible'],valid_from=dates[0],valid_until=dates[1],
        total_quantity=body['totalQuantity'],self_claim_limit=body['selfClaimLimit'],
        claim_mode=body['claimMode'],issuance_enabled=body['issuanceEnabled'])


def campaign_data(campaign, names=None):
    return {'id':str(campaign.id),'code':campaign.code,'title':campaign.title,'kind':campaign.kind,
        'minGoodsFen':campaign.min_goods_fen,'discountFen':campaign.discount_fen,
        'productIds':campaign.product_ids,'productNames':coupon_product_names(campaign.product_ids) if names is None else names,
        'redeemEligible':campaign.redeem_eligible,'validFrom':campaign.valid_from.isoformat(),
        'validUntil':campaign.valid_until.isoformat(),'status':campaign.status,'revision':campaign.revision,
        'totalQuantity':campaign.total_quantity,'issuedQuantity':campaign.issued_quantity,
        'remainingQuantity':campaign.total_quantity-campaign.issued_quantity,
        'selfClaimLimit':campaign.self_claim_limit,'claimMode':campaign.claim_mode,
        'issuanceEnabled':campaign.issuance_enabled}


def get_campaign(campaign_id, *, lock=False):
    query=CouponCampaign.objects.select_for_update() if lock else CouponCampaign.objects
    row=query.filter(pk=campaign_id).first()
    if row is None: raise BenefitError('券活动不存在。','NOT_FOUND',404)
    return row


@transaction.atomic
def create_campaign(body):
    values=fields(body)
    if CouponCampaign.objects.filter(code=values['code']).exists():
        raise BenefitError('活动编号已使用。','COUPON_CODE_EXISTS',409)
    return CouponCampaign.objects.create(status='DRAFT',active=True,**values)


@transaction.atomic
def update_campaign(campaign_id, expected_revision, body):
    values=fields(body); campaign=get_campaign(campaign_id,lock=True)
    _revision(campaign,expected_revision)
    if campaign.status != 'DRAFT': raise BenefitError('发布后券条款不可修改。','COUPON_TERMS_FROZEN',409)
    if CouponCampaign.objects.filter(code=values['code']).exclude(pk=campaign.id).exists():
        raise BenefitError('活动编号已使用。','COUPON_CODE_EXISTS',409)
    for key,value in values.items(): setattr(campaign,key,value)
    campaign.revision+=1; campaign.save()
    return campaign


def _revision(campaign, expected_revision):
    if campaign.revision != revision_value(expected_revision):
        raise BenefitError('券活动已被修改，请刷新后重新确认。','REVISION_CONFLICT',409)


@transaction.atomic
def publish_campaign(campaign_id,expected_revision):
    campaign=get_campaign(campaign_id,lock=True); _revision(campaign,expected_revision)
    if campaign.status != 'DRAFT': raise BenefitError('仅草稿活动可以发布。','COUPON_STATE_INVALID',409)
    if campaign.valid_until <= timezone.now(): raise BenefitError('活动已过期，不能发布。','COUPON_EXPIRED',409)
    campaign.status='PUBLISHED'; campaign.revision+=1; campaign.save(update_fields=['status','revision'])
    return campaign


@transaction.atomic
def set_distribution(campaign_id,expected_revision,enabled):
    if type(enabled) is not bool: raise BenefitError('发放开关须为布尔值。','VALIDATION_FAILED')
    campaign=get_campaign(campaign_id,lock=True); _revision(campaign,expected_revision)
    if campaign.status != 'PUBLISHED': raise BenefitError('仅已发布活动可以暂停或恢复发放。','COUPON_STATE_INVALID',409)
    campaign.issuance_enabled=enabled; campaign.revision+=1
    campaign.save(update_fields=['issuance_enabled','revision'])
    return campaign


def _member_lock(member):
    locked=lock_consumption_member(member.id)
    if not locked.enabled or locked.wechat_app_id != settings.WECHAT_MINI_APP_ID:
        raise BenefitError('会员当前不可领取或发券。','MEMBER_UNAVAILABLE',409)
    return locked


def _distribution(campaign,mode,quantity,at):
    if campaign.status != 'PUBLISHED' or not campaign.issuance_enabled or not campaign.active:
        raise BenefitError('活动暂未开放发放。','COUPON_DISTRIBUTION_CLOSED',409)
    if not campaign.valid_from <= at < campaign.valid_until:
        raise BenefitError('活动不在有效领取时间内。','COUPON_CLAIM_TIME_INVALID',409)
    if campaign.claim_mode not in {mode,'BOTH'}:
        raise BenefitError('活动不支持此领取方式。','COUPON_CLAIM_MODE_INVALID',409)
    if campaign.issued_quantity+quantity > campaign.total_quantity:
        raise BenefitError('活动优惠券余量不足。','COUPON_QUOTA_EXHAUSTED',409)


def _issue_locked(member,campaign,key,quantity,reason,kind,actor,body_hash,allow_repeat):
    allocation,_=CouponAllocation.objects.get_or_create(member=member,campaign=campaign)
    if kind=='SELF' and allocation.self_count >= campaign.self_claim_limit:
        raise BenefitError('已达到本活动领取上限。','COUPON_SELF_LIMIT',409)
    repeated=kind=='ADMIN' and (quantity>1 or allocation.admin_count>0)
    if repeated and (not allow_repeat or not reason):
        raise BenefitError('重复发放须具备授权并填写原因。','COUPON_REPEAT_PERMISSION',403)
    ids=[uuid4() for _ in range(quantity)]
    result={'campaignId':str(campaign.id),'couponIds':[str(value) for value in ids],'quantity':quantity}
    if kind=='ADMIN': result={**result,'memberId':str(member.id)}
    campaign.issued_quantity+=quantity; campaign.save(update_fields=['issued_quantity'])
    allocation.self_count+=quantity if kind=='SELF' else 0
    allocation.admin_count+=quantity if kind=='ADMIN' else 0
    allocation.save(update_fields=['self_count','admin_count'])
    issuance=CouponIssuance.objects.create(member=member,campaign=campaign,actor=actor,kind=kind,
        request_key=key,body_hash=body_hash,quantity=quantity,reason=reason,requires_repeat=repeated,result=result)
    MemberCoupon.objects.bulk_create([MemberCoupon(id=value,member=member,campaign=campaign,issuance=issuance) for value in ids])
    return result


@transaction.atomic
def claim_coupon(member,campaign_id,key):
    key=_uuid(key,'请求编号'); member=_member_lock(member)
    body_hash=fingerprint('claim',campaign_id,{})
    previous=CouponIssuance.objects.filter(member=member,kind='SELF',request_key=key).first()
    if previous:
        if previous.body_hash != body_hash: raise BenefitError('同一请求编号不能用于不同活动。','IDEMPOTENCY_CONFLICT',409)
        return previous.result
    campaign=get_campaign(campaign_id,lock=True); _distribution(campaign,'SELF',1,timezone.now())
    return _issue_locked(member,campaign,key,1,'','SELF',None,body_hash,False)


@transaction.atomic
def issue_coupons(member,campaign_id,key,quantity=1,reason='',*,actor=None,allow_repeat=False,expected_revision=None):
    if type(quantity) is not int or not 1<=quantity<=100 or not isinstance(reason,str) or len(reason)>200:
        raise BenefitError('发放数量或原因格式不正确。','VALIDATION_FAILED')
    if actor is None:
        raise BenefitError('后台发放须有真实操作员。','OPERATOR_REQUIRED',403)
    reason=reason.strip(); key=_uuid(key,'请求编号'); member=_member_lock(member)
    body_hash=fingerprint('issue',campaign_id,{'memberId':str(member.id),'quantity':quantity,'reason':reason})
    from accounts.security import operation_permissions
    actor_permissions=operation_permissions(actor)
    if not {'coupon.read','coupon.issue','member.read'} <= set(actor_permissions):
        raise BenefitError('当前操作员无发放权限或凭证已失效。','PERMISSION_DENIED',403)
    previous=CouponIssuance.objects.filter(actor=actor,kind='ADMIN',request_key=key).first()
    if previous:
        if previous.requires_repeat and 'coupon.issue.repeat' not in actor_permissions:
            raise BenefitError('当前操作员无重复发放权限。','PERMISSION_DENIED',403)
        if previous.body_hash!=body_hash: raise BenefitError('请求编号已用于不同发放内容。','IDEMPOTENCY_CONFLICT',409)
        return previous.result
    campaign=get_campaign(campaign_id,lock=True)
    from accounts.security import operation_permissions
    actor_permissions=operation_permissions(actor)
    if not {'coupon.read','coupon.issue','member.read'} <= set(actor_permissions):
        raise BenefitError('当前操作员无发放权限或凭证已失效。','PERMISSION_DENIED',403)
    allow_repeat=allow_repeat and 'coupon.issue.repeat' in actor_permissions
    if expected_revision is not None: _revision(campaign,expected_revision)
    _distribution(campaign,'ADMIN',quantity,timezone.now())
    return _issue_locked(member,campaign,key,quantity,reason,'ADMIN',actor,body_hash,allow_repeat)
