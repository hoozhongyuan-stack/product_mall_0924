"""D4 authorized operations and authenticated member views."""
from uuid import UUID
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers
from django.db import transaction, connection
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from common.http import offset_response, method
from accounts.security import require, response as base_response, error as base_error, parse_json, confirm_action, audit, permissions
from benefits.policy import rules_data, current_policy, validate_rules, update_rules_locked, body_digest
from benefits.service import BenefitError
from .models import Member, MemberRuleChange
from .auth import require_member
from .operations import member_rows, point_rows, consumption_rows


def response(*args, **kwargs):
    result = base_response(*args, **kwargs)
    result['Cache-Control'] = 'private, no-store'
    return result


def error(*args, **kwargs):
    result = base_error(*args, **kwargs)
    result['Cache-Control'] = 'private, no-store'
    return result


def _failure(request,exc):
    return error(request,getattr(exc,'status',400),getattr(exc,'code','VALIDATION_FAILED'),str(exc))


def _page(request,extra=()):
    if set(request.GET)-{'page','pageSize',*extra}:
        raise ValueError('查询参数不正确。')
    if any(len(request.GET.getlist(key))!=1 for key in request.GET):
        raise ValueError('查询参数不可重复。')
    try: page=int(request.GET.get('page','1')); size=int(request.GET.get('pageSize','20'))
    except ValueError: raise ValueError('分页须为整数。')
    if not 1<=page<=100000 or not 1<=size<=100: raise ValueError('分页参数超出范围。')
    return page,size


def _list_result(request,rows,total,page,size):
    return offset_response(request,{'items':rows,'pagination':{'page':page,'pageSize':size,'total':total}}, pagination_key='pagination', respond=response)


@cache_control(private=True, no_store=True)
def member_list_view(request):
    bad=method(request,'GET')
    if bad:return bad
    _,bad=require(request,'member.read')
    if bad:return bad
    try:
        page,size=_page(request,('search','gradeId','enabled'))
        query=Member.objects.select_related('grade').order_by('-created_at','-id')
        search=request.GET.get('search','').strip()
        if len(search)>64: raise ValueError('搜索会员编号最多 64 字。')
        if search:
            # UUID cast is PostgreSQL-only; never search private WeChat identity.
            from django.db.models.functions import Cast
            from django.db.models import CharField
            query=query.annotate(public_id=Cast('id',output_field=CharField())).filter(public_id__icontains=search)
        if request.GET.get('gradeId'):
            try: grade_id=UUID(request.GET['gradeId'])
            except ValueError: raise ValueError('等级格式不正确。')
            query=query.filter(grade_id=grade_id)
        if 'enabled' in request.GET:
            if request.GET['enabled'] not in ('true','false'): raise ValueError('会员状态格式不正确。')
            query=query.filter(enabled=request.GET['enabled']=='true')
        total=query.count()
        from catalog.models import MemberGrade
        from .operations import grade_data
        return offset_response(request,{"items":member_rows(query[(page-1)*size:page*size]),
            "pagination":{"page":page,"pageSize":size,"total":total},
            "grades":[grade_data(row) for row in MemberGrade.objects.filter(enabled=True).order_by("rank")]}, pagination_key='pagination', respond=response)
    except ValueError as exc:return _failure(request,exc)


def _admin_member(request,member_id):
    actor,bad=require(request,'member.read')
    if bad:return None,bad
    row=Member.objects.select_related('grade').filter(pk=member_id).first()
    return (row,None) if row else (None,error(request,404,'MEMBER_NOT_FOUND','会员不存在。'))


@cache_control(private=True, no_store=True)
def member_detail_view(request,member_id):
    bad=method(request,'GET')
    if bad:return bad
    row,bad=_admin_member(request,member_id)
    if bad:return bad
    if request.GET:return error(request,400,'VALIDATION_FAILED','查询参数不正确。')
    return response(request,{**member_rows([row])[0],'ruleRevision':current_policy()['revision']})


def _events(request,member_id,kind,admin):
    bad=method(request,'GET')
    if bad:return bad
    row,bad=_admin_member(request,member_id) if admin else require_member(request)
    if bad:return bad
    try:
        page,size=_page(request)
        rows,total=(point_rows if kind=='points' else consumption_rows)(row.id,page,size)
        return _list_result(request,rows,total,page,size)
    except ValueError as exc:return _failure(request,exc)


@cache_control(private=True, no_store=True)
def member_points_view(request,member_id):return _events(request,member_id,'points',True)
@cache_control(private=True, no_store=True)
def member_consumption_view(request,member_id):return _events(request,member_id,'consumption',True)
@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers("Authorization")
def app_points_view(request):return _events(request,None,'points',False)
@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers("Authorization")
def app_consumption_view(request):return _events(request,None,'consumption',False)


@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers("Authorization")
def app_overview_view(request):
    bad=method(request,'GET')
    if bad:return bad
    row,bad=require_member(request)
    if bad:return bad
    if request.GET:return error(request,400,'VALIDATION_FAILED','查询参数不正确。')
    rules=rules_data()
    return response(request,{**member_rows([row])[0],'ruleRevision':rules['revision'],'rules':rules})


@cache_control(private=True, no_store=True)
def member_rules_view(request):
    bad=method(request,'GET','PUT')
    if bad:return bad
    actor,bad=require(request)
    if not bad and not (('member.rules.manage' in permissions(actor)) or
        (request.method=='GET' and 'member.rules.read' in permissions(actor))):
        _,bad=require(request,'member.rules.manage' if request.method=='PUT' else 'member.rules.read')
    if bad:return bad
    if request.GET:return error(request,400,'VALIDATION_FAILED','查询参数不正确。')
    if request.method=='GET':return response(request,rules_data())
    try:
        body=parse_json(request)
        request_key=UUID(request.headers.get('Idempotency-Key',''))
        validate_rules(body)
        digest=body_digest(body)
        with transaction.atomic():
            # Serialize all edits before password-token consumption and policy write.
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)',[174004000])
            actor,denied=require(request,'member.rules.manage')
            if denied:return denied
            prior=MemberRuleChange.objects.filter(actor=actor,request_key=request_key).first()
            if prior:
                if prior.request_digest!=digest:raise BenefitError('请求标识对应其他规则。','IDEMPOTENCY_CONFLICT',409)
                return response(request,prior.after)
            before=rules_data()
            if before['revision']!=body['expectedRevision']:
                raise BenefitError('规则已变化，请刷新后核对。','REVISION_CONFLICT',409)
            denied=confirm_action(request,actor,'member.rules.update','member-rules',before['revision'])
            if denied:return denied
            after=update_rules_locked(body)
            MemberRuleChange.objects.create(actor=actor,request_key=request_key,request_digest=digest,
                revision=after['revision'],before=before,after=after,reason=body['reason'].strip())
            audit(request,'member.rules.update','member-rules','member-rules',actor,
                before=before,after={**after,'reason':body['reason'].strip()})
        return response(request,after)
    except ValueError as exc:return _failure(request,exc)
