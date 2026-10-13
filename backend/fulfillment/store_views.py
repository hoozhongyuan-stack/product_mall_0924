"""Store-scoped member operations; all read and write paths check staff membership."""
from uuid import UUID
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers
from accounts.security import response, error, parse_json
from common.http import method
from customers.auth import require_member
from orders.models import Order
from orders.service import order_data
from .service import FulfillmentError
from .store_service import operate
from stores.views import guarded


@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization', 'Cookie')
@guarded
def orders_view(request, store_id, order_id=None):
    bad = method(request, 'GET', *(['POST'] if order_id else []))
    if bad:
        return bad
    member, denied = require_member(request)
    if denied:
        return denied
    from stores.access import StoreError, require_staff
    try:
        require_staff(member, store_id, permission='orders')
        if request.method == 'POST':
            try:
                key = UUID(request.headers.get('Idempotency-Key', ''))
            except (ValueError, AttributeError) as exc:
                raise FulfillmentError('请提供防重复请求标识。') from exc
            operate(member, store_id, order_id, parse_json(request), key)
        if order_id:
            row = scoped_orders(store_id).filter(pk=order_id).first()
            if not row:
                raise FulfillmentError('订单不存在。', 'ORDER_NOT_FOUND', 404)
            return response(request, scoped_order_data(row, store_id))
        if set(request.GET) - {'page', 'status', 'fulfillment'} or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
            raise FulfillmentError('订单筛选参数不正确。')
        try:
            page = int(request.GET.get('page', '1'))
        except ValueError as exc:
            raise FulfillmentError('页码不正确。') from exc
        if not 1 <= page <= 10000:
            raise FulfillmentError('页码不正确。')
        rows = scoped_orders(store_id).order_by('-created_at', '-id')
        state = request.GET.get('status')
        if state:
            if state not in ('PENDING_PAYMENT', 'PAID', 'CLOSED'):
                raise FulfillmentError('订单状态不正确。')
            rows = rows.filter(status=state)
        from .task_reads import TASK_FIELDS, filter_order_tasks
        task=request.GET.get('fulfillment')
        if task:
            if task not in TASK_FIELDS:
                raise FulfillmentError('履约任务不正确。')
            rows=filter_order_tasks(rows,task,store_id=store_id)
        total=rows.count()
        page_rows=list(rows.prefetch_related('lines')[(page-1)*20:page*20])
        from .read import page_fulfillment_summaries
        facts=page_fulfillment_summaries(page_rows, store_id=store_id)
        items=[{'orderId':str(row.id),'orderNo':row.order_no,'status':row.status,
            'storeId':str(row.store_id) if row.store_id else None,'storeName':row.store_snapshot.get('name'),
            'deliveryMode':row.delivery_mode,'revision':row.revision,'payableFen':row.payable_fen,
            'createdAt':row.created_at.isoformat(),'items':[{'orderLineId':str(line.id),'name':line.product_name,
                'quantity':line.quantity,'saleUnit':line.sale_unit} for line in row.lines.all()],
            **facts[row.id]} for row in page_rows]
        return response(request, {'items':items,'total':total,'page':page,'pageSize':20})
    except (FulfillmentError, StoreError) as exc:
        return error(request, exc.status, exc.code, str(exc))
    except ValueError as exc:
        return error(request, 400, 'VALIDATION_FAILED', str(exc))


@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization', 'Cookie')
@guarded
def aftersales_view(request, store_id, case_id=None):
    bad=method(request,'GET',*(['POST'] if case_id else []))
    if bad:
        return bad
    member,denied=require_member(request)
    if denied:
        return denied
    from stores.access import StoreError,require_staff
    from aftersales.models import AfterSaleCase
    from aftersales.api_read import case_data
    from aftersales.store_service import record_note,notes_data
    from aftersales.service import AfterSaleError
    try:
        require_staff(member,store_id,'orders')
        if request.method=='POST':
            try:
                key=UUID(request.headers.get('Idempotency-Key',''))
            except (ValueError,AttributeError) as exc:
                raise AfterSaleError('请提供防重复请求标识。') from exc
            record_note(member,store_id,case_id,parse_json(request),key)
        rows=scoped_aftersales(store_id).select_related('order_line__order').order_by('-created_at')
        if case_id:
            case=rows.filter(pk=case_id).first()
            if not case:
                raise AfterSaleError('售后单不存在。','AFTERSALE_NOT_FOUND',404)
            data=case_data(case,admin=False)
            data.update(storeNotes=notes_data(case),canWithdraw=False,canSubmitReturnShipment=False,canRecordReceipt=case.status=='WAITING_RETURN',platformReviewRequired=True)
            return response(request,data)
        if set(request.GET) - {'page', 'status'} or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
            raise AfterSaleError('售后筛选参数不正确。')
        if 'status' in request.GET:
            if request.GET['status'] != 'ACTIVE':
                raise AfterSaleError('售后状态不正确。')
            from aftersales.service import ACTIVE
            rows = rows.filter(status__in=ACTIVE)
        try:
            page=int(request.GET.get('page','1'))
        except ValueError as exc:
            raise AfterSaleError('页码不正确。') from exc
        if not 1 <= page <= 10000:
            raise AfterSaleError('页码不正确。')
        return response(request,{'items':[{**case_data(row,detail=False),'canWithdraw':False} for row in rows[(page-1)*20:page*20]],'total':rows.count(),'page':page,'pageSize':20})
    except (AfterSaleError,StoreError) as exc:
        return error(request,exc.status,exc.code,str(exc))
    except ValueError as exc:
        return error(request,400,'VALIDATION_FAILED',str(exc))


@csrf_exempt
@cache_control(private=True, no_store=True)
@vary_on_headers('Authorization', 'Cookie')
@guarded
def redemptions_view(request, store_id, order_id):
    bad = method(request, 'POST')
    if bad:
        return bad
    member, denied = require_member(request)
    if denied:
        return denied
    try:
        from .redemption import store_redeem
        key = UUID(request.headers.get('Idempotency-Key', ''))
        row = store_redeem(member, store_id, order_id, parse_json(request), key)
        return response(request, scoped_order_data(row, store_id))
    except FulfillmentError as exc:
        return error(request, exc.status, exc.code, str(exc))
    except (ValueError, AttributeError):
        return error(request, 400, 'VALIDATION_FAILED', '核销请求或防重复标识不正确。')


def scoped_orders(store_id):
    from django.db.models import Q
    return Order.objects.filter(Q(store_id=store_id, delivery_mode__in=['PICKUP', 'DELIVERY']) |
        Q(lines__fulfillment_kind='REDEEM', lines__redeem_scope_snapshot__scope__in=['STORE', 'BOTH'],
          lines__redeem_scope_snapshot__contains={'stores': [{'storeId': str(store_id)}]})).distinct()


def scoped_order_data(order, store_id):
    from .redemption import permitted
    from .read import page_fulfillment_summaries
    data = order_data(order)
    if data.get('storeDelivery') and str(order.store_id) != str(store_id):
        data = {**data, 'storeDelivery': {**data['storeDelivery'], 'events': []}}
    lines = {str(line.id): line for line in order.lines.all()}
    for item in data['items']:
        progress = item.get('fulfillment') or {}
        if progress.get('kind') == 'REDEEM':
            progress['canRedeemStore'] = progress.get('canRedeemStore', False) and permitted(lines[item['orderLineId']], store_id)
    return {**data, **page_fulfillment_summaries([order], store_id=store_id)[order.id]}


def scoped_aftersales(store_id):
    from django.db.models import Q
    from aftersales.models import AfterSaleCase
    return AfterSaleCase.objects.filter(Q(order_line__order__store_id=store_id, order_line__order__delivery_mode__in=['PICKUP', 'DELIVERY']) |
        Q(order_line__fulfillment_kind='REDEEM', order_line__redeem_scope_snapshot__scope__in=['STORE', 'BOTH'],
          order_line__redeem_scope_snapshot__contains={'stores': [{'storeId': str(store_id)}]}))
