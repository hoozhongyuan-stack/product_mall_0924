"""Bounded coupon DTOs. Never expose actors or internal issuance keys to members."""
from django.db.models import F, Q
from django.db.models.functions import Least, Coalesce
from django.utils import timezone
from catalog.coupon_targets import coupon_product_names
from .models import CouponCampaign, CouponAllocation, MemberCoupon
from .coupon_operations import campaign_data
from .service import BenefitError


def paging(request,allowed):
    if any(len(values)!=1 for _,values in request.GET.lists()):
        raise BenefitError('查询条件不能重复。','VALIDATION_FAILED')
    if set(request.GET)-set(allowed)-{'page','pageSize'}:
        raise BenefitError('不支持此查询条件。','VALIDATION_FAILED')
    try: page=int(request.GET.get('page','1')); size=int(request.GET.get('pageSize','20'))
    except ValueError: raise BenefitError('分页格式不正确。','VALIDATION_FAILED')
    if not 1<=page<=100000 or not 1<=size<=100:
        raise BenefitError('分页超出范围。','VALIDATION_FAILED')
    return page,size


def paginated(query,page,size,convert):
    total=query.count(); rows=list(query[(page-1)*size:page*size])
    ids={value for row in rows for value in row.product_ids}
    names={row['id']:row for row in coupon_product_names(ids)}
    return {'items':[convert(row,[names[value] for value in row.product_ids if value in names]) for row in rows],
            'pagination':{'page':page,'pageSize':size,'total':total}}


def campaign_rows(request):
    page,size=paging(request,{'q','status'})
    query=CouponCampaign.objects.all()
    search=request.GET.get('q','').strip()
    if len(search)>100: raise BenefitError('关键词过长。','VALIDATION_FAILED')
    if search: query=query.filter(Q(title__icontains=search)|Q(code__icontains=search))
    status=request.GET.get('status','')
    if status:
        if status not in CouponCampaign.State.values: raise BenefitError('活动状态不正确。','VALIDATION_FAILED')
        query=query.filter(status=status)
    return paginated(query.order_by('-id'),page,size,campaign_data)


def claimable_rows(request,member):
    page,size=paging(request,set()); at=timezone.now()
    query=CouponCampaign.objects.filter(status='PUBLISHED',active=True,issuance_enabled=True,
        claim_mode__in=['SELF','BOTH'],valid_from__lte=at,valid_until__gt=at,issued_quantity__lt=F('total_quantity'))
    total=query.count(); rows=list(query.order_by('valid_until','id')[(page-1)*size:page*size])
    counts=dict(CouponAllocation.objects.filter(member=member,campaign_id__in=[row.id for row in rows])
                .values_list('campaign_id','self_count'))
    names={row['id']:row for row in coupon_product_names({value for row in rows for value in row.product_ids})}
    items=[]
    for row in rows:
        full=campaign_data(row,[names[value] for value in row.product_ids if value in names])
        safe={key:full[key] for key in ['id','title','kind','minGoodsFen','discountFen','productIds','productNames',
            'redeemEligible','validFrom','validUntil','remainingQuantity','selfClaimLimit']}
        count=counts.get(row.id,0)
        items.append({**safe,'selfClaimedCount':count,'canClaim':count<row.self_claim_limit})
    return {'items':items,'pagination':{'page':page,'pageSize':size,'total':total}}


def member_coupon_rows(request,member):
    page,size=paging(request,{'status'}); at=timezone.now()
    state=request.GET.get('status','ALL')
    if state not in {'ALL','AVAILABLE','RESERVED','USED','EXPIRED'}:
        raise BenefitError('优惠券状态不正确。','VALIDATION_FAILED')
    query=MemberCoupon.objects.select_related('campaign').filter(member=member).exclude(campaign__status='DRAFT')
    query=query.annotate(effective_until=Least(F('campaign__valid_until'),Coalesce(F('restored_valid_until'),F('campaign__valid_until'))))
    if state=='EXPIRED': query=query.filter(status='AVAILABLE',effective_until__lte=at)
    elif state=='AVAILABLE': query=query.filter(status='AVAILABLE',effective_until__gt=at)
    elif state in {'RESERVED','USED'}: query=query.filter(status=state)
    total=query.count(); rows=list(query.order_by('-issued_at','-id')[(page-1)*size:page*size])
    names={row['id']:row for row in coupon_product_names({value for row in rows for value in row.campaign.product_ids})}
    items=[]
    for row in rows:
        campaign=row.campaign
        data=campaign_data(campaign,[names[value] for value in campaign.product_ids if value in names])
        data={key:data[key] for key in ['title','kind','minGoodsFen','discountFen','productIds','productNames','redeemEligible','validFrom']}
        items.append({**data,'id':str(row.id),'campaignId':str(campaign.id),
            'validUntil':row.effective_until.isoformat(),'status':'EXPIRED' if row.status=='AVAILABLE' and row.effective_until<=at else row.status,
            'issuedAt':row.issued_at.isoformat(),'orderId':str(row.reserved_order_id) if row.reserved_order_id else None})
    return {'items':items,'pagination':{'page':page,'pageSize':size,'total':total}}
