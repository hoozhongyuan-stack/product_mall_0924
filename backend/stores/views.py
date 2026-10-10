"""Admin cookie/CSRF and member bearer surfaces are kept distinct."""
from functools import wraps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers
from django.views.decorators.debug import sensitive_variables
from common.http import response,error,method,parse_json_object
from accounts.security import require,require_live,audit
from customers.auth import require_member
from customers.models import Member
from .models import Store,StoreStaff
from .access import StoreError,get_store,require_staff
from .service import store_data,save_store,products_data,update_product,account_data,account_summaries


def guarded(function):
    @wraps(function)
    def inner(request,*args,**kwargs):
        try:
            from .quota import allowed
            if not allowed(request,function.__name__):
                return error(request,429,'RATE_LIMITED','操作过于频繁，请稍后再试。')
            return function(request,*args,**kwargs)
        except StoreError as exc:
            return error(request,exc.status,exc.code,str(exc))
        except (ValueError,TypeError,ValidationError):
            return error(request,400,'INVALID_INPUT','请求参数格式不正确。')
    return inner


@guarded
def admin_stores(request,store_id=None):
    bad = method(request,*(['GET','PATCH'] if store_id else ['GET','POST']))
    if bad: return bad
    actor,bad = require(request,'stores.read' if request.method=='GET' else 'stores.manage')
    if bad: return bad
    if request.method=='GET':
        if store_id: return response(request,store_data(get_store(store_id),True))
        rows = Store.objects.select_related('warehouse').all()
        query = request.GET.get('search',request.GET.get('q','')).strip()[:120]
        if query: rows=rows.filter(name__icontains=query)
        page,size,start=page_bounds(request)
        return response(request,{'items':[store_data(row,True) for row in rows[start:start+size]],'total':rows.count(),'page':page,'pageSize':size})
    with transaction.atomic():
        if store_id:
            if not Store.objects.select_for_update().filter(pk=store_id).first():
                raise StoreError('门店不存在。','STORE_NOT_FOUND',404)
        actor,bad=require_live(request,'stores.manage')
        if bad:return bad
        store = save_store(parse_json_object(request),get_store(store_id) if store_id else None)
        audit(request,'stores.save','store',store.id,actor=actor,after=store_data(store,True))
    return response(request,store_data(store,True),200 if store_id else 201)


def page_bounds(request):
    page=int(request.GET.get('page',1))
    size=int(request.GET.get('pageSize',20))
    if page<1 or not 1<=size<=100:raise StoreError('分页参数无效。')
    return page,size,(page-1)*size


def staff_data(row):
    return {'id':str(row.id),'memberId':str(row.member_id),'name':row.member.nickname or row.member.member_no,'memberNo':row.member.member_no,'permissions':row.permissions,'enabled':row.enabled,'revision':row.revision}


@guarded
def admin_staff(request,store_id):
    bad = method(request,'GET','POST')
    if bad: return bad
    actor,bad = require(request,'stores.read' if request.method=='GET' else 'stores.manage')
    if bad: return bad
    store = get_store(store_id)
    if request.method=='GET':
        return response(request,{'items':[staff_data(row) for row in StoreStaff.objects.filter(store=store).select_related('member')]})
    body = parse_json_object(request)
    if set(body)!={'memberId','permissions','enabled'} or type(body['enabled']) is not bool or not isinstance(body['permissions'],list) or any(item not in ['products','orders','accounts'] for item in body['permissions']):
        raise StoreError('门店员工权限设置无效。')
    member = Member.objects.filter(pk=body['memberId'],enabled=True).first()
    if not member: raise StoreError('该会员不存在或已停用。')
    with transaction.atomic():
        Store.objects.select_for_update().get(pk=store.id)
        actor,bad=require_live(request,'stores.manage')
        if bad:return bad
        row,created = StoreStaff.objects.get_or_create(store=store,member=member)
        row.permissions = sorted(set(body['permissions']))
        row.enabled = body['enabled']
        row.revision += 0 if created else 1
        row.save()
        audit(request,'stores.staff.assign','store_staff',row.id,actor=actor,after=staff_data(row))
    return response(request,staff_data(row))


@cache_control(private=True,no_store=True)
@vary_on_headers('Cookie')
@guarded
def admin_accounts(request,store_id=None):
    bad = method(request,'GET')
    if bad: return bad
    actor,bad = require(request,'stores.accounts.read')
    if bad: return bad
    if store_id: return response(request,account_data(get_store(store_id)))
    rows = Store.objects.order_by('name','id')
    page,size,start=page_bounds(request)
    return response(request,{'items':account_summaries(rows[start:start+size]),'total':rows.count(),'page':page,'pageSize':size})


@guarded
def public_stores(request,store_id=None):
    bad=method(request,'GET')
    if bad: return bad
    lat,lng = request.GET.get('latitude'),request.GET.get('longitude')
    if (lat is None)!=(lng is None): raise StoreError('经纬度须同时提供。')
    if lat is not None:
        lat,lng=float(lat),float(lng)
        import math
        if not math.isfinite(lat) or not math.isfinite(lng) or abs(lat)>90 or abs(lng)>180: raise StoreError('定位坐标无效。')
    if store_id:
        store=get_store(store_id)
        if not store.enabled: raise StoreError('门店不存在。','STORE_NOT_FOUND',404)
        return response(request,store_data(store,latitude=lat,longitude=lng))
    rows=Store.objects.filter(enabled=True,warehouse__enabled=True)
    query=request.GET.get('q','').strip()[:120]
    city=request.GET.get('city','').strip()[:80]
    if query: rows=rows.filter(name__icontains=query)
    if city: rows=rows.filter(city=city)
    if lat is not None:
        # Rank before the response limit, so a nearby store outside the first
        # alphabetical page remains eligible for automatic matching.
        from django.db.models import F, Value, FloatField
        from django.db.models.functions import ASin, Cast, Cos, Least, Power, Radians, Sin, Sqrt
        import math
        phi = Radians(Cast(F('latitude'),FloatField()))
        theta = Radians(Cast(F('longitude'),FloatField()))
        haversine = Power(Sin((phi-Value(math.radians(lat)))/2),2) + Value(math.cos(math.radians(lat)))*Cos(phi)*Power(Sin((theta-Value(math.radians(lng)))/2),2)
        distance = 12742000*ASin(Sqrt(Least(Value(1.0),haversine)))
        rows = rows.annotate(nearest_distance=distance).order_by(F('nearest_distance').asc(nulls_last=True),'id')
    items=[store_data(row,latitude=lat,longitude=lng) for row in rows[:200]]
    if lat is not None: items.sort(key=lambda item:item.get('distanceMeters',float('inf')))
    return response(request,{'items':items,'total':rows.count()})


@csrf_exempt
@guarded
def member_stores(request):
    bad=method(request,'GET')
    if bad: return bad
    member,bad=require_member(request)
    if bad: return bad
    rows=StoreStaff.objects.filter(member=member,enabled=True).select_related('store','store__warehouse')
    return response(request,{'items':[{**store_data(row.store),'permissions':row.permissions} for row in rows]})


@csrf_exempt
@guarded
def member_products(request,store_id,product_id=None):
    bad=method(request,*(['PATCH'] if product_id else ['GET']))
    if bad: return bad
    member,bad=require_member(request)
    if bad: return bad
    with transaction.atomic():
        # Serialize membership reassignment and stock updates on the store boundary.
        if not Store.objects.select_for_update().filter(pk=store_id).first():
            raise StoreError('门店不存在。','STORE_NOT_FOUND',404)
        store=require_staff(member,store_id,'products')
        if request.method=='GET':
            from catalog.models import Product
            page,size,start=page_bounds(request)
            query=request.GET.get('search','').strip()[:120]
            queryset=Product.objects.filter(fulfillment_kind='SHIP').exclude(status='DRAFT')
            if query:queryset=queryset.filter(name__icontains=query)
            return response(request,{'items':products_data(store,offset=start,limit=size,query=query),'page':page,'pageSize':size,'total':queryset.count()})
        return response(request,update_product(store,product_id,parse_json_object(request),member))


@sensitive_variables('body')
@cache_control(private=True,no_store=True)
@vary_on_headers('Authorization')
@csrf_exempt
@guarded
def member_account(request,store_id):
    bad=method(request,'GET','POST')
    if bad: return bad
    member,bad=require_member(request)
    if bad: return bad
    store=require_staff(member,store_id,'accounts')
    if request.method=='POST':
        from payments.store_withdrawals import request_withdrawal,withdrawal_data
        row,created=request_withdrawal(store,member,parse_json_object(request,max_bytes=4096))
        return response(request,withdrawal_data(row),201 if created else 200)
    return response(request,account_data(store))
