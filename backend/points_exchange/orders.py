"""Pure points quote and atomic order coordination. No money provider calls."""
import hashlib,json
from datetime import timedelta
from uuid import UUID,uuid4
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from catalog.exchange_access import exchange_catalog_rows
from customers.consumption import lock_consumption_member
from customers.exchange_access import exchange_address_snapshot
from inventory.availability import default_available_base_units
from inventory.models import Warehouse
from inventory.reservations import lock_default_balances,reserve_order_lines,ReservationError
from benefits.service import available_exchange_points,reserve_exchange_points,BenefitError
from benefits.policy import current_policy
from orders.models import Order,OrderLine,OrderIdempotency
from orders.service import OrderError,order_data,settle_paid_order_locked
from .models import ExchangeOffer,ExchangeQuote


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def identity(value,label):
    try: return UUID(value) if isinstance(value,str) else value if isinstance(value,UUID) else UUID('invalid')
    except (ValueError,TypeError): raise OrderError(label+'格式不正确。')


def _member(member):
    current=lock_consumption_member(member.id)
    if not current.enabled or current.auth_version!=member.auth_version or current.wechat_app_id!=settings.WECHAT_MINI_APP_ID:
        raise OrderError('登录已失效，请重新登录。','SESSION_EXPIRED',401)
    return current


def _address(member,address_id,shipping):
    if not shipping: return {}
    if address_id is None: raise OrderError('请选择收货地址。','ADDRESS_REQUIRED',409)
    snapshot=exchange_address_snapshot(member,address_id)
    if snapshot is None: raise OrderError('收货地址已失效，请重新选择。','ADDRESS_CHANGED',409)
    return snapshot


def _address_dto(snapshot):
    return dict(snapshot)


def _offer(offer_id,*,lock=False):
    query=ExchangeOffer.objects.select_for_update() if lock else ExchangeOffer.objects
    offer=query.filter(pk=offer_id).first()
    if not offer: raise OrderError('积分商品不存在。','EXCHANGE_OFFER_NOT_FOUND',404)
    return offer


def _snapshot(offer,row,quantity,warehouse,address_id,address):
    return {'offerId':str(offer.id),'offerRevision':offer.revision,'skuId':str(offer.sku_id),
        'pointsUnitPrice':offer.points_price,'quantity':quantity,'totalPoints':offer.points_price*quantity,
        'warehouseId':str(warehouse.id),'addressId':str(address_id) if address_id else None,'address':address,
        'line':{key:row[key] for key in ['skuId','productId','name','skuCode','specs','saleUnit','ratio','unitVersionId','fulfillmentKind','redeemValidUntil','imageUrl']}}


@transaction.atomic
def create_exchange_quote(member,body,*,authorize=None):
    if set(body)-{'offerId','quantity','addressId'} or not {'offerId','quantity'}<=set(body): raise OrderError('兑换报价字段不正确。')
    offer_id=identity(body['offerId'],'积分商品');quantity=body['quantity']
    if type(quantity) is not int or not 1<=quantity<=99: raise OrderError('兑换数量须为 1 至 99 的整数。')
    member=_member(member)
    if authorize: authorize()
    offer=_offer(offer_id)
    row=exchange_catalog_rows([offer.sku_id]).get(offer.sku_id)
    if offer.status!='ON_SALE' or not row or not row['eligible']: raise OrderError('积分商品暂不可兑换。','EXCHANGE_OFFER_UNAVAILABLE',409)
    warehouse,balances=default_available_base_units([offer.sku_id])
    if not warehouse: raise OrderError('默认仓暂不可用。','WAREHOUSE_UNAVAILABLE',409)
    address_id=identity(body['addressId'],'地址') if body.get('addressId') is not None else None
    address=_address(member,address_id,row['fulfillmentKind']=='SHIP')
    snapshot=_snapshot(offer,row,quantity,warehouse,address_id,address)
    if ExchangeQuote.objects.filter(member=member,created_at__gte=timezone.now()-timedelta(minutes=15)).count()>=60:
        raise OrderError('报价操作过多，请稍后重试。','RATE_LIMITED',429)
    expires=timezone.now()+timedelta(minutes=10)
    quote=ExchangeQuote.objects.create(member=member,offer=offer,address_id=address_id,snapshot=snapshot,digest=digest(snapshot),expires_at=expires)
    available=available_exchange_points(member,timezone.now());stock=balances.get(offer.sku_id,0)//row['ratio']
    if authorize: authorize()
    blocked='OUT_OF_STOCK' if quantity>stock else 'POINTS_UNAVAILABLE' if snapshot['totalPoints']>available else ''
    return {'quoteId':str(quote.id),'expiresAt':expires.isoformat(),'orderKind':'POINTS','offerId':str(offer.id),
        'quantity':quantity,'pointsUnitPrice':offer.points_price,'totalPoints':snapshot['totalPoints'],
        'availablePoints':available,'shippingFeeFen':0,'ready':not blocked,
        'exchangeOrderAvailable':bool(getattr(settings,'EXCHANGE_ORDER_ENABLED',False)),
        'blockedCode':blocked,'blockedMessage':{'OUT_OF_STOCK':'商品库存不足。','POINTS_UNAVAILABLE':'可用积分不足。'}.get(blocked,''),
        'addressId':str(address_id) if address_id else None,'line':{**snapshot['line'],'availableQuantity':stock}}


def _key_lock(member_id,key):
    from django.db import connection
    value=int.from_bytes(hashlib.sha256(f'EXCHANGE_ORDER:{member_id}:{key}'.encode()).digest()[:8],'big',signed=True)
    with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_xact_lock(%s)',[value])


def _revalidate(member,quote):
    snapshot=quote.snapshot
    if quote.digest!=digest(snapshot): raise OrderError('兑换报价快照不一致。','QUOTE_CHANGED',409)
    if quote.expires_at<=timezone.now(): raise OrderError('报价已过期，请重新核对。','QUOTE_EXPIRED',409)
    offer=_offer(quote.offer_id)
    row=exchange_catalog_rows([offer.sku_id],lock=True).get(offer.sku_id)
    offer=_offer(quote.offer_id,lock=True)
    warehouse=Warehouse.objects.filter(pk=snapshot['warehouseId'],is_default=True,enabled=True).first()
    if not warehouse: raise OrderError('默认仓已变化，请重新报价。','WAREHOUSE_CHANGED',409)
    address=_address(member,quote.address_id,row['fulfillmentKind']=='SHIP') if row else {}
    if not row or not row['eligible'] or offer.status!='ON_SALE': raise OrderError('积分商品已变化。','EXCHANGE_OFFER_UNAVAILABLE',409)
    current=_snapshot(offer,row,snapshot['quantity'],warehouse,quote.address_id,address)
    if current!=snapshot: raise OrderError('积分商品、单位、价格或地址已变化，请重新报价。','QUOTE_CHANGED',409)
    return offer,row,warehouse


def _create_order(member,quote,row,warehouse,policy):
    snapshot=quote.snapshot;qty=snapshot['quantity'];unit=row['unit']
    order=Order.objects.create(order_kind='POINTS',order_no='P'+uuid4().hex.upper(),member=member,quote_id=quote.id,
        payment_method='POINTS',address_snapshot=_address_dto(snapshot['address']),goods_total_fen=0,shipping_fee_fen=0,
        benefit_policy_snapshot=policy,points_to_use=snapshot['totalPoints'],points_discount_fen=0,payable_fen=0,
        expires_at=quote.expires_at)
    from fulfillment.service import snapshot_order_policy_locked
    from aftersales.service import snapshot_order_policy_locked as snapshot_after_sale
    snapshot_order_policy_locked(order);snapshot_after_sale(order)
    line=OrderLine.objects.create(order=order,sku=row['sku'],product_id=row['productId'],warehouse=warehouse,
        unit_version=unit,product_name=row['name'],sku_code=row['skuCode'],specs_snapshot=row['specs'],
        fulfillment_kind=row['fulfillmentKind'],redeem_valid_until=row['redeemValidUntil'],base_unit=unit.base_unit,
        sale_unit=unit.sale_unit,ratio=unit.ratio,quantity=qty,base_quantity=qty*unit.ratio,
        points_unit_price=snapshot['pointsUnitPrice'],points_total=snapshot['totalPoints'],
        unit_price_fen=0,goods_amount_fen=0,payable_fen=0)
    balances=lock_default_balances(warehouse.id,[line.sku_id]);reserve_order_lines([line],balances)
    reserve_exchange_points(member,order.id,order.points_to_use)
    from benefits.lifecycle import snapshot_order_benefits_locked
    snapshot_order_benefits_locked(order);settle_paid_order_locked(order)
    return order


@transaction.atomic
def submit_exchange_order(member,body,key,*,authorize=None):
    if set(body)!={'quoteId'}: raise OrderError('兑换订单只接受报价标识。')
    quote_id=identity(body['quoteId'],'报价');key=identity(key,'请求标识');_key_lock(member.id,key)
    member=_member(member)
    if authorize: authorize()
    request_digest=digest({'quoteId':str(quote_id)})
    quote=ExchangeQuote.objects.filter(pk=quote_id,member=member).first()
    if not quote: raise OrderError('报价不存在或不可访问。','QUOTE_NOT_FOUND',404)
    prior=OrderIdempotency.objects.select_related('order').filter(member=member,scope='EXCHANGE_ORDER',key=key).first()
    if prior:
        if prior.request_digest!=request_digest: raise OrderError('请求标识已用于不同兑换内容。','IDEMPOTENCY_CONFLICT',409)
        return order_data(prior.order,include_voucher_code=True),True
    if not getattr(settings,'EXCHANGE_ORDER_ENABLED',False): raise OrderError('积分兑换暂未开放。','EXCHANGE_NOT_READY',503)
    policy=current_policy(lock=True);offer,row,warehouse=_revalidate(member,quote)
    try: order=_create_order(member,quote,row,warehouse,policy)
    except (ReservationError,BenefitError) as exc: raise OrderError(str(exc),exc.code,409) from exc
    if authorize: authorize()
    OrderIdempotency.objects.create(member=member,scope='EXCHANGE_ORDER',key=key,request_digest=request_digest,order=order)
    return order_data(order,include_voucher_code=True),False


def exchange_order_result(member,key):
    prior=OrderIdempotency.objects.select_related('order').filter(member=member,scope='EXCHANGE_ORDER',key=key).first()
    return {'status':'COMPLETED','result':order_data(prior.order,include_voucher_code=True)} if prior else {'status':'NOT_FOUND'}
