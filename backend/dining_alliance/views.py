"""Distinct admin CSRF/session and member bearer APIs."""
from functools import wraps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_control
from common.http import method, response, error, parse_json_object, offset_response
from accounts.security import require, require_live, audit
from customers.auth import require_member
from customers.store_access import resolve_staff_member, search_staff_members
from catalog.models import Product
from stores.access import StoreError
from .access import DiningError, get_restaurant, require_staff, get_session, checkout_context
from .models import Restaurant, RestaurantStaff, DiningTable, DiningSession, DiningOrderSnapshot, ProductCommissionRule
from .service import restaurant_data, save_restaurant, table_data, save_table, products_data, product_queryset, update_product, scan
from .finance import account_data, withdrawal_data, request_withdrawal, change_withdrawal


def guarded(fn):
    @wraps(fn)
    @cache_control(private=True,no_store=True)
    def inner(request,*args,**kwargs):
        try:
            from stores.quota import allowed
            if not allowed(request,'dining_'+fn.__name__):
                return error(request,429,'RATE_LIMITED','操作过于频繁，请稍后重试。')
            return fn(request,*args,**kwargs)
        except (DiningError, StoreError) as exc:
            return error(request,exc.status,exc.code,str(exc))
        except (ValueError,TypeError,ValidationError):
            return error(request,400,'INVALID_INPUT','请求参数格式不正确。')
        except IntegrityError:
            return error(request,409,'DINING_CONFLICT','数据已变化或重复，请刷新后重试。')
    return inner


def page_data(request,rows,serialize):
    page=int(request.GET.get('page',1));size=int(request.GET.get('pageSize',20))
    if not 1<=page<=10000 or not 1<=size<=100:
        raise DiningError('分页参数无效。')
    return {'items':[serialize(row) for row in rows[(page-1)*size:page*size]],'total':rows.count(),'page':page,'pageSize':size}


@guarded
def admin_restaurants(request,restaurant_id=None):
    bad=method(request,*(['GET','PATCH'] if restaurant_id else ['GET','POST']))
    if bad:return bad
    actor,bad=require(request,'dining.read' if request.method=='GET' else 'dining.manage')
    if bad and request.method=='GET' and restaurant_id is None:
        actor,bad=require(request,'dining.finance.read')
    if bad:return bad
    if request.method=='GET':
        if restaurant_id:return response(request,restaurant_data(get_restaurant(restaurant_id)))
        rows=Restaurant.objects.select_related('assigned_store').all()
        query=request.GET.get('search','').strip()[:120]
        if query:rows=rows.filter(name__icontains=query)
        if 'enabled' in request.GET:
            value=request.GET['enabled']
            if len(request.GET.getlist('enabled'))!=1 or value not in ('true','false'):raise DiningError('启停筛选参数无效。')
            rows=rows.filter(enabled=value=='true')
        if 'storeId' in request.GET:
            from uuid import UUID
            if len(request.GET.getlist('storeId'))!=1:raise DiningError('前置仓筛选参数不可重复。')
            rows=rows.filter(assigned_store_id=UUID(request.GET['storeId']))
        return offset_response(request,page_data(request,rows,restaurant_data))
    with transaction.atomic():
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        row=save_restaurant(parse_json_object(request),restaurant_id)
        data=restaurant_data(row)
        audit(request,'dining.restaurant.save','restaurant',row.id,actor=actor,after=data)
    return response(request,data,200 if restaurant_id else 201)


@guarded
def admin_tables(request,restaurant_id,table_id=None):
    bad=method(request,*(['PATCH'] if table_id else ['GET','POST']))
    if bad:return bad
    actor,bad=require(request,'dining.read' if request.method=='GET' else 'dining.manage')
    if bad:return bad
    get_restaurant(restaurant_id)
    if request.method=='GET':
        return offset_response(request,page_data(request,DiningTable.objects.filter(restaurant_id=restaurant_id),table_data))
    with transaction.atomic():
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        row=save_table(restaurant_id,parse_json_object(request),table_id)
        audit(request,'dining.table.save','dining_table',row.id,actor=actor,after=table_data(row))
    return response(request,table_data(row),200 if table_id else 201)


def staff_data(row):
    return {'id':str(row.id),'memberId':str(row.member_id),'memberNo':row.member.member_no,'name':row.member.nickname or row.member.member_no,'permissions':row.permissions,'enabled':row.enabled,'revision':row.revision}


@guarded
def admin_staff(request,restaurant_id):
    bad=method(request,'GET','POST')
    if bad:return bad
    actor,bad=require(request,'dining.read' if request.method=='GET' else 'dining.manage')
    if bad:return bad
    get_restaurant(restaurant_id)
    if request.method=='GET':
        if 'memberSearch' in request.GET:
            actor,bad=require(request,'dining.manage')
            if bad:return bad
            return response(request,{'members':search_staff_members(request.GET['memberSearch'])})
        return offset_response(request,page_data(request,RestaurantStaff.objects.filter(restaurant_id=restaurant_id).select_related('member').order_by('id'),staff_data))
    body=parse_json_object(request)
    if set(body)!={'memberId','permissions','enabled','revision'} or type(body['enabled']) is not bool or type(body.get('revision')) is not int or not isinstance(body['permissions'],list) or any(not isinstance(p,str) or p not in ('products','orders','finance') for p in body['permissions']):
        raise DiningError('餐饮店管理员权限无效。')
    with transaction.atomic():
        restaurant=get_restaurant(restaurant_id,lock=True)
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        member=resolve_staff_member(body['memberId'])
        row,created=RestaurantStaff.objects.get_or_create(restaurant=restaurant,member=member)
        if body['revision'] != (0 if created else row.revision):
            raise DiningError('管理员授权已变化，请刷新。','REVISION_CONFLICT',409)
        row.permissions=sorted(set(body['permissions']));row.enabled=body['enabled'];row.revision+=0 if created else 1;row.save()
        audit(request,'dining.staff.save','dining_staff',row.id,actor=actor,after=staff_data(row))
    return response(request,staff_data(row))


def ratio_data(product):
    row=ProductCommissionRule.objects.filter(product=product).first()
    return {'id':str(product.id),'productId':str(product.id),'name':product.name,'productNo':product.product_no,'categoryId':str(product.category_id),'commissionBps':row.commission_bps if row else None,'revision':row.revision if row else 0}


@guarded
def admin_ratios(request):
    bad=method(request,'GET','PUT','PATCH')
    if bad:return bad
    actor,bad=require(request,'dining.read' if request.method=='GET' else 'dining.manage')
    if bad:return bad
    if request.method=='GET':
        rows=Product.objects.filter(fulfillment_kind='SHIP').exclude(status='DRAFT').order_by('name','id')
        query=request.GET.get('search','').strip()[:120]
        if query:rows=rows.filter(Q(name__icontains=query)|Q(product_no__icontains=query))
        if 'configured' in request.GET:
            value=request.GET['configured']
            if len(request.GET.getlist('configured'))!=1 or value not in ('true','false'):raise DiningError('佣金配置筛选参数无效。')
            rows=rows.filter(productcommissionrule__isnull=value=='false')
        if 'categoryId' in request.GET:
            from uuid import UUID
            from catalog.models import Category
            if len(request.GET.getlist('categoryId'))!=1:raise DiningError('分类参数不可重复。')
            category=UUID(request.GET['categoryId'])
            if not Category.objects.filter(pk=category).exists():raise DiningError('分类不存在。','DINING_NOT_FOUND',404)
            rows=rows.filter(Q(category_id=category)|Q(category__parent_id=category))
        return offset_response(request,page_data(request,rows,ratio_data))
    body=parse_json_object(request)
    if set(body)!={'productId','commissionBps','revision'} or type(body['commissionBps']) is not int or not 0<=body['commissionBps']<=10000 or type(body['revision']) is not int:
        raise DiningError('商品佣金比例或修订号无效。')
    with transaction.atomic():
        product=Product.objects.select_for_update().filter(pk=body['productId'],fulfillment_kind='SHIP').first()
        if product is None:raise DiningError('商品不存在。','DINING_NOT_FOUND',404)
        actor,bad=require_live(request,'dining.manage')
        if bad:return bad
        row=ProductCommissionRule.objects.filter(product=product).first()
        if body['revision']!=(row.revision if row else 0):raise DiningError('佣金比例已变化。','REVISION_CONFLICT',409)
        if row:
            row.commission_bps=body['commissionBps'];row.revision+=1;row.save()
        else:row=ProductCommissionRule.objects.create(product=product,commission_bps=body['commissionBps'])
        data=ratio_data(product)
        audit(request,'dining.ratio.save','dining_ratio',product.id,actor=actor,after=data)
    return response(request,data)


@csrf_exempt
@guarded
def member_restaurants(request):
    bad=method(request,'GET')
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    rows=RestaurantStaff.objects.filter(member=member,enabled=True).select_related('restaurant__assigned_store')
    return response(request,{'items':[{**restaurant_data(row.restaurant),'permissions':row.permissions} for row in rows]})


@csrf_exempt
@guarded
def member_products(request,restaurant_id):
    bad=method(request,'GET','PUT','PATCH')
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    with transaction.atomic():
        restaurant=require_staff(member,restaurant_id,'products',lock=True)
        if request.method!='GET':return response(request,update_product(restaurant,parse_json_object(request)))
        page=int(request.GET.get('page',1));size=int(request.GET.get('pageSize',20))
        if not 1<=page<=10000 or not 1<=size<=100:raise DiningError('分页参数无效。')
        total=Product.objects.filter(fulfillment_kind='SHIP').exclude(status='DRAFT').count()
        return offset_response(request,{'items':products_data(restaurant,member,offset=(page-1)*size,limit=size),'total':total,'page':page,'pageSize':size})


@csrf_exempt
@guarded
def scan_view(request):
    bad=method(request,'POST')
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    body=parse_json_object(request,max_bytes=4096)
    if set(body)!={'tableCode'}:raise DiningError('扫码参数无效。')
    return response(request,scan(member,body['tableCode']),201)


@csrf_exempt
@guarded
def session_view(request,session_id,products=False):
    bad=method(request,'GET' if products else 'DELETE')
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    if products:
        store,context=checkout_context(session_id,member)
        restaurant=get_restaurant(context['restaurantId'])
        page=int(request.GET.get('page',1));size=int(request.GET.get('pageSize',100))
        if not 1<=page<=10000 or not 1<=size<=100:raise DiningError('分页参数无效。')
        return offset_response(request,{'items':products_data(restaurant,member,customer=True,offset=(page-1)*size,limit=size),'page':page,'pageSize':size,'total':product_queryset(restaurant,customer=True).count()})
    with transaction.atomic():
        row=DiningSession.objects.select_for_update().filter(pk=session_id,member=member,member_auth_version=member.auth_version).first()
        if row is None:raise DiningError('扫码会话不存在。','DINING_NOT_FOUND',404)
        from django.utils import timezone
        if row.closed_at is None:
            row.closed_at=timezone.now();row.save(update_fields=['closed_at'])
    return response(request,{'closed':True})


def order_data(snapshot):
    from .finance import income_data
    from fulfillment.store_service import delivery_data
    order=snapshot.order
    return {'id':str(order.id),'orderNo':order.order_no,'status':order.status,'payableFen':order.payable_fen,'createdAt':order.created_at.isoformat(),'restaurantId':str(snapshot.restaurant_id),'restaurantName':snapshot.restaurant_name,'tableNumber':snapshot.table_number,'address':snapshot.address,'fulfillmentStore':{'id':str(snapshot.store_id),'name':order.store_snapshot.get('name') or '历史仓名未记录'},'fulfillmentWarehouse':order.store_snapshot.get('fulfillmentWarehouse'),'commission':income_data(snapshot),'commissionLines':[{'lineId':str(row.line_id),'quantity':row.quantity,'paidFen':row.paid_fen,'purchaseCostFen':row.purchase_cost_fen,'costSource':row.cost_source,'commissionBps':row.commission_bps,'commissionFen':row.commission_fen,'configured':row.configured} for row in snapshot.lines.all()],'delivery':delivery_data(order),'lines':[{'id':str(line.id),'productName':line.product_name,'skuCode':line.sku_code,'quantity':line.quantity,'saleUnit':line.sale_unit,'payableFen':line.payable_fen} for line in order.lines.all()]}


@guarded
def admin_orders(request,order_id=None):
    bad=method(request,'GET')
    if bad:return bad
    _,bad=require(request,'dining.read')
    if bad:return bad
    return orders_response(request,order_id=order_id,restaurant_id=request.GET.get('restaurantId'))


@csrf_exempt
@guarded
def member_orders(request,restaurant_id,order_id=None):
    bad=method(request,'GET')
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    require_staff(member,restaurant_id,'orders')
    return orders_response(request,order_id,restaurant_id)


def orders_response(request,order_id=None,restaurant_id=None):
    rows=DiningOrderSnapshot.objects.select_related('order','store').order_by('-created_at','order_id')
    if restaurant_id:rows=rows.filter(restaurant_id=restaurant_id)
    if request.GET.get('storeId'):rows=rows.filter(store_id=request.GET['storeId'])
    search=request.GET.get('search','').strip()
    if len(search)>40:raise DiningError('订单号搜索不超过 40 字。')
    if search:rows=rows.filter(order__order_no__icontains=search)
    if order_id:
        row=rows.filter(order_id=order_id).first()
        if not row:raise DiningError('订单不存在。','DINING_NOT_FOUND',404)
        return response(request,order_data(row))
    return offset_response(request,page_data(request,rows,order_data))


@guarded
def admin_finance(request,restaurant_id,withdrawals=False,withdrawal_id=None,action=None):
    bad=method(request,'POST' if action else 'GET')
    if bad:return bad
    actor,bad=require(request,'dining.finance.manage' if action else 'dining.finance.read')
    if bad:return bad
    restaurant=get_restaurant(restaurant_id)
    if action:
        with transaction.atomic():
            actor,bad=require_live(request,'dining.finance.manage')
            if bad:return bad
            row=change_withdrawal(restaurant_id,withdrawal_id,action,parse_json_object(request))
            audit(request,'dining.withdrawal.'+action,'dining_withdrawal',row.id,actor=actor,after=withdrawal_data(row))
        return response(request,withdrawal_data(row))
    data=account_data(restaurant)
    return response(request,{'items':data['withdrawals']} if withdrawals else data)


@csrf_exempt
@guarded
def member_finance(request,restaurant_id,withdrawals=False):
    bad=method(request,*(['GET','POST'] if withdrawals else ['GET']))
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    restaurant=require_staff(member,restaurant_id,'finance')
    if request.method=='POST':
        row,created=request_withdrawal(restaurant_id,member,parse_json_object(request))
        return response(request,withdrawal_data(row),201 if created else 200)
    data=account_data(restaurant)
    return response(request,{'items':data['withdrawals']} if withdrawals else data)


@guarded
def admin_order_stores(request):
    bad=method(request,'GET')
    if bad:return bad
    _,bad=require(request,'dining.read')
    if bad:return bad
    from stores.models import Store
    rows=Store.objects.filter(
        Q(id__in=Restaurant.objects.values('assigned_store_id')) |
        Q(id__in=DiningOrderSnapshot.objects.values('store_id'))
    ).order_by('name','id')
    return offset_response(request,page_data(request,rows,lambda row:{'id':str(row.pk),'name':row.name}))
