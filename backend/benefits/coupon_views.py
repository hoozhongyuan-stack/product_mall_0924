"""Admin CSRF-protected distribution operations and bearer-only member reads."""
from uuid import UUID,uuid4
from functools import wraps
from django.db import connection, transaction, IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.utils.cache import patch_vary_headers
from common.http import offset_response, method
from accounts.security import (require,require_live,permissions,parse_json,response as base_response,error,confirm_action,audit)
from customers.auth import require_member
from customers.models import Member
from catalog.coupon_targets import search_product_options
from .models import CouponCampaign,CouponIssuance,CouponOperation
from .service import BenefitError,_uuid
from .coupon_operations import (campaign_data,get_campaign,create_campaign,update_campaign,
    publish_campaign,set_distribution,claim_coupon,issue_coupons,fingerprint,revision_value,EDITABLE)
from .coupon_reads import paging,campaign_rows,claimable_rows,member_coupon_rows

def response(request,data=None,status=200,**kwargs):
    result=base_response(request,data,status,**kwargs)
    result['Cache-Control']='no-store'
    return result


PERMISSIONS={'create':'coupon.manage','edit':'coupon.manage','publish':'coupon.publish',
             'distribution':'coupon.publish','issue':'coupon.issue'}


def private_response(view):
    @wraps(view)
    def wrapper(request,*args,**kwargs):
        request.request_id=uuid4()
        result=view(request,*args,**kwargs)
        result['Cache-Control']='no-store'
        patch_vary_headers(result,['Authorization','Cookie'])
        return result
    return wrapper


def _no_query(request):
    return error(request,400,'VALIDATION_FAILED','此接口不接受查询参数。') if request.GET else None


def _method(request,*methods):
    return method(request,*methods)


def _deny(request,exc):
    return error(request,exc.status,exc.code,str(exc))


def _read(request,*codes):
    account,bad=require_live(request,'coupon.read')
    if bad: return None,bad
    for code in codes:
        if code not in permissions(account):
            return require_live(request,code)
    return account,None


def _key(request):
    return _uuid(request.headers.get('Idempotency-Key'),'请求编号')


def _operation_lock(actor,key):
    # One scoped key is serialized even when concurrent requests name different campaigns.
    value=int(fingerprint(str(actor.id),key,{})[:16],16)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)',[value if value<2**63 else value-2**64])


def _validate_body(action,body):
    expected={'create':EDITABLE,'edit':EDITABLE|{'expectedRevision'},
        'publish':{'expectedRevision'},'distribution':{'expectedRevision','issuanceEnabled'},
        'issue':{'expectedRevision','memberId','quantity','reason'}}[action]
    if set(body)!=expected: raise BenefitError('请求字段不完整或包含未支持的字段。','VALIDATION_FAILED')
    if action!='create': revision_value(body['expectedRevision'])


def _execute_admin(actor,action,campaign_id,body,key):
    if action=='create': campaign=create_campaign(body); result=campaign_data(campaign)
    elif action=='edit':
        campaign=update_campaign(campaign_id,body['expectedRevision'],{key:value for key,value in body.items() if key!='expectedRevision'})
        result=campaign_data(campaign)
    elif action=='publish': campaign=publish_campaign(campaign_id,body['expectedRevision']); result=campaign_data(campaign)
    elif action=='distribution':
        campaign=set_distribution(campaign_id,body['expectedRevision'],body['issuanceEnabled']); result=campaign_data(campaign)
    else:
        member_id=_uuid(body['memberId'],'会员')
        member=Member.objects.filter(pk=member_id).first()
        if member is None: raise BenefitError('会员不存在。','NOT_FOUND',404)
        result=issue_coupons(member,campaign_id,key,body['quantity'],body['reason'],actor=actor,
            allow_repeat='coupon.issue.repeat' in permissions(actor),expected_revision=body['expectedRevision'])
        campaign=get_campaign(campaign_id)
    return campaign,result


def _admin_write(request,actor,action,campaign_id=None):
    body=parse_json(request); _validate_body(action,body); key=_key(request)
    body_hash=fingerprint(action,campaign_id,body)
    with transaction.atomic():
        _operation_lock(actor,key)
        current,bad=_read(request,PERMISSIONS[action],*(['member.read'] if action=='issue' else []))
        if bad: return bad
        prior=CouponOperation.objects.filter(actor=actor,request_key=key).first()
        if prior:
            proof=CouponIssuance.objects.filter(actor=actor,request_key=key,kind='ADMIN').first() if prior.action=='issue' else None
            if proof and proof.requires_repeat:
                current,bad=_read(request,'coupon.issue.repeat')
                if bad: return bad
            if prior.body_hash!=body_hash: raise BenefitError('请求编号已用于不同内容。','IDEMPOTENCY_CONFLICT',409)
            return response(request,prior.result,201 if action in {'create','issue'} else 200)
        if campaign_id:
            if action=='issue':
                from customers.consumption import lock_consumption_member
                member_id=_uuid(body['memberId'],'会员')
                if not Member.objects.filter(pk=member_id).exists():
                    raise BenefitError('会员不存在。','NOT_FOUND',404)
                lock_consumption_member(member_id)
            get_campaign(campaign_id,lock=True)
            current,bad=_read(request,PERMISSIONS[action],*(['member.read'] if action=='issue' else []))
            if bad: return bad
            if action=='issue':
                from .models import CouponAllocation
                previous_count=CouponAllocation.objects.filter(member_id=member_id,campaign_id=campaign_id).values_list('admin_count',flat=True).first() or 0
                if type(body['quantity']) is int and (body['quantity']>1 or previous_count>0):
                    current,bad=_read(request,'coupon.issue.repeat')
                    if bad: return bad
        if action in {'publish','distribution','issue'}:
            bad=confirm_action(request,actor,'coupon.issue' if action=='issue' else 'coupon.publish',
                               campaign_id,body['expectedRevision'])
            if bad: return bad
        before=campaign_data(get_campaign(campaign_id)) if campaign_id else {}
        campaign,result=_execute_admin(actor,action,campaign_id,body,key)
        CouponOperation.objects.create(actor=actor,request_key=key,action=action,campaign=campaign,body_hash=body_hash,result=result)
        audit(request,'coupon.'+action,'coupon_campaign',campaign.id,actor=actor,before=before,after=result)
        return response(request,result,201 if action in {'create','issue'} else 200)


def _write_view(request,action,campaign_id=None):
    account,bad=_read(request,PERMISSIONS[action],*(['member.read'] if action=='issue' else []))
    if bad: return bad
    try: return _admin_write(request,account,action,campaign_id)
    except BenefitError as exc: return _deny(request,exc)
    except (ValueError,TypeError) as exc: return error(request,400,'VALIDATION_FAILED','请求内容格式不正确。')
    except IntegrityError: return error(request,409,'COUPON_CONFLICT','活动已被其他操作修改，请核对后重试。')


@private_response
def campaigns_view(request):
    bad=_method(request,'GET','POST')
    if bad: return bad
    if request.method=='POST': return _write_view(request,'create')
    account,bad=_read(request)
    if bad: return bad
    try: return offset_response(request,campaign_rows(request), pagination_key='pagination', respond=response)
    except BenefitError as exc: return _deny(request,exc)


@private_response
def campaign_detail_view(request,campaign_id):
    bad=_method(request,'GET','PUT')
    if bad: return bad
    if request.method=='PUT': return _write_view(request,'edit',campaign_id)
    account,bad=_read(request)
    if bad: return bad
    bad=_no_query(request)
    if bad: return bad
    try: return response(request,campaign_data(get_campaign(campaign_id)))
    except BenefitError as exc: return _deny(request,exc)


@private_response
def campaign_publish_view(request,campaign_id):
    return _method(request,'POST') or _write_view(request,'publish',campaign_id)


@private_response
def campaign_distribution_view(request,campaign_id):
    return _method(request,'POST') or _write_view(request,'distribution',campaign_id)


@private_response
def campaign_issuances_view(request,campaign_id):
    bad=_method(request,'GET','POST')
    if bad: return bad
    if request.method=='POST': return _write_view(request,'issue',campaign_id)
    account,bad=_read(request)
    if bad: return bad
    try:
        page,size=paging(request,set()); get_campaign(campaign_id)
        query=CouponIssuance.objects.filter(campaign_id=campaign_id).select_related('actor').order_by('-created_at','-id')
        total=query.count()
        items=[{'id':str(row.id),'memberId':str(row.member_id),'memberLabel':'会员 '+str(row.member_id)[:8],
                'kind':row.kind,'quantity':row.quantity,'reason':row.reason,
                'actorLabel':row.actor.display_name if row.actor else '会员本人','createdAt':row.created_at.isoformat()}
               for row in query[(page-1)*size:page*size]]
        return offset_response(request,{'items':items,'pagination':{'page':page,'pageSize':size,'total':total}}, pagination_key='pagination', respond=response)
    except BenefitError as exc: return _deny(request,exc)


@private_response
def campaign_product_options_view(request):
    bad=_method(request,'GET')
    if bad: return bad
    account,bad=_read(request,'coupon.manage')
    if bad: return bad
    try:
        page,size=paging(request,{'q'}); search=request.GET.get('q','').strip()
        if len(search)>100: raise BenefitError('关键词过长。','VALIDATION_FAILED')
        items,total=search_product_options(search,page,size)
        return offset_response(request,{'items':items,'pagination':{'page':page,'pageSize':size,'total':total}}, pagination_key='pagination', respond=response)
    except BenefitError as exc: return _deny(request,exc)


@private_response
def admin_coupon_operation_view(request,request_key):
    bad=_method(request,'GET')
    if bad: return bad
    account,bad=_read(request)
    if bad: return bad
    bad=_no_query(request)
    if bad: return bad
    row=CouponOperation.objects.filter(actor=account,request_key=request_key).first()
    if row:
        account,bad=_read(request,PERMISSIONS[row.action],*(['member.read'] if row.action=='issue' else []))
        if bad: return bad
        proof=CouponIssuance.objects.filter(actor=account,request_key=request_key,kind='ADMIN').first() if row.action=='issue' else None
        if proof and proof.requires_repeat:
            account,bad=_read(request,'coupon.issue.repeat')
            if bad: return bad
    return response(request,{'status':'COMPLETED','result':row.result} if row else {'status':'NOT_FOUND'})


@csrf_exempt
@private_response
def app_campaigns_view(request):
    bad=_method(request,'GET')
    if bad: return bad
    member,bad=require_member(request)
    if bad: return bad
    try: return offset_response(request,claimable_rows(request,member), pagination_key='pagination', respond=response)
    except BenefitError as exc: return _deny(request,exc)


@csrf_exempt
@private_response
def app_claim_view(request,campaign_id):
    bad=_method(request,'POST')
    if bad: return bad
    member,bad=require_member(request)
    if bad: return bad
    try:
        if parse_json(request)!={}: raise BenefitError('领取请求不接受额外字段。','VALIDATION_FAILED')
        with transaction.atomic():
            from customers.consumption import lock_consumption_member
            lock_consumption_member(member.id)
            current,bad=require_member(request)
            if bad: return bad
            return response(request,claim_coupon(current,campaign_id,_key(request)),201)
    except BenefitError as exc: return _deny(request,exc)
    except ValueError as exc: return error(request,400,'VALIDATION_FAILED',str(exc))


@csrf_exempt
@private_response
def app_coupon_claim_view(request,request_key):
    bad=_method(request,'GET')
    if bad: return bad
    member,bad=require_member(request)
    if bad: return bad
    bad=_no_query(request)
    if bad: return bad
    row=CouponIssuance.objects.filter(member=member,kind='SELF',request_key=request_key).first()
    return response(request,{'status':'COMPLETED','result':row.result} if row else {'status':'NOT_FOUND'})


@csrf_exempt
@private_response
def app_coupons_view(request):
    bad=_method(request,'GET')
    if bad: return bad
    member,bad=require_member(request)
    if bad: return bad
    try: return offset_response(request,member_coupon_rows(request,member), pagination_key='pagination', respond=response)
    except BenefitError as exc: return _deny(request,exc)
