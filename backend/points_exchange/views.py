"""CSRF admin endpoints and bearer-protected points order endpoints."""
from functools import wraps
from uuid import uuid4
from django.db import IntegrityError
from django.http import HttpResponseNotAllowed
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_exempt
from accounts.security import require_live,parse_json,response,error
from customers.auth import resolve_member
from orders.service import OrderError
from .models import ExchangeOffer
from .operations import list_offers,offer_rows,sku_options,operate,operation_result
from .orders import identity,create_exchange_quote,submit_exchange_order,exchange_order_result


def endpoint(*methods):
    def decorate(view):
        @wraps(view)
        def wrapper(request,*args,**kwargs):
            request.request_id=uuid4()
            try: result=HttpResponseNotAllowed(methods) if request.method not in methods else view(request,*args,**kwargs)
            except OrderError as exc: result=error(request,exc.status,exc.code,str(exc),exc.details)
            except (ValueError,TypeError): result=error(request,400,'VALIDATION_FAILED','请求内容格式不正确。')
            except IntegrityError: result=error(request,409,'EXCHANGE_CONFLICT','积分商品已被其他操作修改，请核对后重试。')
            result['Cache-Control']='no-store';patch_vary_headers(result,['Authorization','Cookie'])
            return result
        return wrapper
    return decorate


def _admin(request,code='exchange.read'):
    actor,bad=require_live(request,'exchange.read')
    if bad:return actor,bad
    return require_live(request,code)


def _member(request):
    member=resolve_member(request)
    if member is None: raise OrderError('请先登录。','LOGIN_REQUIRED',401)
    return member


def _no_query(request):
    if request.GET:raise OrderError('此接口不接受查询条件。')


def _write(request,action,offer_id=None):
    result,bad,replay=operate(request,action,parse_json(request),identity(request.headers.get('Idempotency-Key'),'请求标识'),offer_id)
    return bad or response(request,result,200 if replay or action!='create' else 201)


@endpoint('GET','POST')
def offers_view(request):
    actor,bad=_admin(request)
    if bad:return bad
    return _write(request,'create') if request.method=='POST' else response(request,list_offers(request.GET))


@endpoint('GET','PUT')
def offer_detail_view(request,offer_id):
    actor,bad=_admin(request)
    if bad:return bad
    if request.method=='PUT':return _write(request,'edit',offer_id)
    _no_query(request);offer=ExchangeOffer.objects.filter(pk=offer_id).first()
    if not offer:raise OrderError('积分商品不存在。','EXCHANGE_OFFER_NOT_FOUND',404)
    return response(request,offer_rows([offer])[0])


@endpoint('POST')
def offer_availability_view(request,offer_id):
    return _write(request,'availability',offer_id)


@endpoint('GET')
def sku_options_view(request):
    actor,bad=_admin(request,'exchange.manage')
    return bad or response(request,sku_options(request.GET))


@endpoint('GET')
def admin_operations_view(request,key):
    _no_query(request);result,bad=operation_result(request,key)
    return bad or response(request,result)


@csrf_exempt
@endpoint('GET')
def products_view(request):
    return response(request,list_offers(request.GET,public=True))


@csrf_exempt
@endpoint('GET')
def product_detail_view(request,offer_id):
    _no_query(request)
    from catalog.exchange_access import eligible_exchange_sku_ids
    offer=ExchangeOffer.objects.filter(pk=offer_id,status='ON_SALE',sku_id__in=eligible_exchange_sku_ids()).first()
    if not offer:raise OrderError('积分商品暂不可查看。','EXCHANGE_OFFER_NOT_FOUND',404)
    return response(request,offer_rows([offer])[0])


@csrf_exempt
@endpoint('POST')
def quotes_view(request):
    member=_member(request)
    return response(request,create_exchange_quote(member,parse_json(request),authorize=lambda:_member(request)),201)


@csrf_exempt
@endpoint('POST')
def orders_view(request):
    member=_member(request);key=identity(request.headers.get('Idempotency-Key'),'请求标识')
    result,replay=submit_exchange_order(member,parse_json(request),key,authorize=lambda:_member(request))
    return response(request,result,200 if replay else 201)


@csrf_exempt
@endpoint('GET')
def member_operations_view(request,key):
    _no_query(request)
    return response(request,exchange_order_result(_member(request),key))
