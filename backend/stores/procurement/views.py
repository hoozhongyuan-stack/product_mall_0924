"""Cookie/CSRF platform actions and separate bearer warehouse operations."""
import uuid
from django.db import transaction
from django.db.models import Q, Prefetch
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from accounts.models import AdminAccount
from accounts.security import require, require_live, confirm_action, audit
from customers.auth import require_member
from customers.models import Member
from catalog.models import Product, Sku
from catalog.spec_read import specification_prefetch, specification_label
from stores.models import Store, StoreSkuProfitRule
from stores.access import require_staff, staff_permissions, StoreError
from stores.views import guarded, page_bounds
from common.http import response, error, method, parse_json_object
from wechat_integration.credentials import cipher
from .models import ProcurementOrder, ProcurementEvent, ProcurementSettings
from .service import create_order, transition, data, fields, text, instructions, integer, seal


def member_scope(member, store_id, permission=None):
    if permission:
        return require_staff(member,store_id,permission)
    grants=staff_permissions(member,store_id)
    if not {'procurement','receiving'} & set(grants):
        raise StoreError('没有本仓进货或收货权限。','STORE_FORBIDDEN',403)
    return Store.objects.get(pk=store_id)


def order_query():
    from .models import ProcurementLine
    return ProcurementOrder.objects.select_related('store','proof').prefetch_related(Prefetch('lines',queryset=ProcurementLine.objects.select_related('sku')))


def find_order(order_id, store_id=None, lock=False):
    rows=order_query()
    if lock: rows=rows.select_for_update(of=('self',))
    row=rows.filter(pk=order_id,**({'store_id':store_id} if store_id else {})).first()
    if not row: raise StoreError('进货单不存在。','PROCUREMENT_NOT_FOUND',404)
    return row


def listing(request, rows, admin=False):
    page,size,start=page_bounds(request)
    q=request.GET.get('q','').strip()
    if len(q)>120: raise StoreError('检索内容过长。')
    if q: rows=rows.filter(Q(store__name__icontains=q)|Q(note__icontains=q)|Q(id__icontains=q.replace('JH','')))
    status=request.GET.get('status')
    if status: rows=rows.filter(status=status)
    store=request.GET.get('storeId')
    if store: rows=rows.filter(store_id=uuid.UUID(store))
    return response(request,{'items':[data(x,admin,summary=True) for x in rows[start:start+size]],'total':rows.count(),'page':page,'pageSize':size})


@csrf_exempt
@cache_control(private=True,no_store=True)
@guarded
def member_products(request,store_id):
    bad=method(request,'GET')
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    member_scope(member,store_id,'procurement')
    page,size,start=page_bounds(request)
    rows=Product.objects.filter(status='ON_SALE',fulfillment_kind='SHIP').order_by('product_no','id')
    q=request.GET.get('q','').strip()
    if len(q)>120: raise StoreError('检索内容过长。')
    if q: rows=rows.filter(Q(name__icontains=q)|Q(product_no__icontains=q))
    sku_query=Sku.objects.filter(sale_status='ON_SALE',current_unit__isnull=False).select_related('current_unit').prefetch_related(specification_prefetch())
    items=[]
    products=list(rows.prefetch_related(Prefetch('skus',queryset=sku_query))[start:start+size])
    rules={x.sku_id:x for x in StoreSkuProfitRule.objects.filter(sku__product_id__in=[p.pk for p in products])}
    for product in products:
        skus=[]
        for sku in product.skus.all():
            rule=rules.get(sku.pk)
            valid=rule and rule.unit_version_id==sku.current_unit_id
            skus.append({'id':str(sku.pk),'specLabel':specification_label(sku),'saleUnit':sku.current_unit.sale_unit,
                'purchaseCostFen':rule.purchase_cost_fen if valid else None,'costRevision':rule.revision if rule else 0,
                'currentUnitVersionId':str(sku.current_unit_id),'conversionRatio':sku.current_unit.ratio,'baseUnit':sku.current_unit.base_unit})
        items.append({'id':str(product.pk),'name':product.name,'skus':skus})
    return response(request,{'items':items,'total':rows.count(),'page':page,'pageSize':size})


@csrf_exempt
@cache_control(private=True,no_store=True)
@guarded
def member_orders(request,store_id,order_id=None,action=None):
    bad=method(request,*(['POST'] if action else ['GET'] if order_id else ['GET','POST']))
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    permission='receiving' if action in ['receive','exception'] else 'procurement' if request.method=='POST' else None
    store=member_scope(member,store_id,permission)
    if request.method=='GET': return response(request,data(find_order(order_id,store_id))) if order_id else listing(request,order_query().filter(store=store))
    body=parse_json_object(request,max_bytes=32768)
    with transaction.atomic():
        Member.objects.select_for_update().get(pk=member.pk)
        member_scope(member,store_id,permission)
        if not order_id: row=create_order(store,member,body)
        else:
            if action not in ['confirm-quote','payment-proof','cancel','exception','receive']: raise StoreError('仓端操作无效。')
            row=transition(find_order(order_id,store_id,True),member,action,body)
    return response(request,data(row),201 if not order_id else 200)


@cache_control(private=True,no_store=True)
@guarded
def admin_orders(request,order_id=None,action=None,request_key=None):
    bad=method(request,'POST' if action else 'GET')
    if bad:return bad
    permission='stores.procurement.finance' if action in ['confirm-payment','close-cancel'] else 'stores.procurement.manage' if action else 'stores.procurement.read'
    actor,bad=require(request,permission)
    if bad:return bad
    if request_key:
        row=find_order(order_id)
        event=ProcurementEvent.objects.filter(order=row,request_key=request_key,admin=actor).first()
        return response(request,{'status':'COMPLETED','result':data(row,True)} if event else {'status':'NOT_FOUND'})
    if not action:return response(request,data(find_order(order_id),True)) if order_id else listing(request,order_query(),True)
    if action not in ['review','reject','confirm-payment','reject-proof','ship','resolve-exception','close-cancel','verify-units']: raise StoreError('平台操作无效。')
    body=parse_json_object(request,max_bytes=4096)
    with transaction.atomic():
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
        actor,bad=require_live(request,permission)
        if bad:return bad
        row=find_order(order_id,lock=True)
        # An exact replay is safe to return without requiring an already-consumed password token.
        replay=ProcurementEvent.objects.filter(order=row,request_key=body.get('requestKey')).first()
        if action in ['confirm-payment','close-cancel'] and not replay:
            bad=confirm_action(request,actor,'stores.procurement.'+action,str(row.pk),row.revision)
            if bad:return bad
        before={'status':row.status,'revision':row.revision}
        row=transition(row,actor,action,body,True)
        if not replay:audit(request,'stores.procurement.'+action,'procurement_order',row.pk,actor,before=before,after={'status':row.status,'revision':row.revision})
    return response(request,data(row,True))


@cache_control(private=True,no_store=True)
@guarded
def settings(request):
    bad=method(request,'GET','PUT')
    if bad:return bad
    actor,bad=require(request,'stores.procurement.read' if request.method=='GET' else 'stores.procurement.manage')
    if bad:return bad
    with transaction.atomic():
        if request.method=='PUT':
            AdminAccount.objects.select_for_update().get(pk=actor.pk)
        row=(ProcurementSettings.objects.select_for_update() if request.method=='PUT' else ProcurementSettings.objects).get(pk=1)
        if request.method=='PUT':
            body=parse_json_object(request,max_bytes=8192)
            fields(body,['paymentInstructions','expectedRevision'])
            value=text(body['paymentInstructions'],2000,True)
            if row.revision!=integer(body['expectedRevision']):return error(request,409,'REVISION_CONFLICT','收款说明已变化，请刷新后再试。')
            actor,bad=require_live(request,'stores.procurement.manage')
            if bad:return bad
            bad=confirm_action(request,actor,'stores.procurement.configure','procurement-settings',row.revision)
            if bad:return bad
            row.encrypted_instructions=seal(value)
            row.revision+=1;row.save()
            audit(request,'stores.procurement.configure','procurement_settings','1',actor,after={'revision':row.revision})
        return response(request,{'paymentInstructions':instructions(row.encrypted_instructions),'revision':row.revision})
