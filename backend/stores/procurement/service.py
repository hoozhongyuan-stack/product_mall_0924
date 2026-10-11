"""Locked procurement facts and immutable SKU/unit snapshots."""
import hashlib
import json
import uuid
from django.db import transaction
from catalog.models import Sku
from catalog.spec_read import specification_label
from customers.models import Member
from stores.access import StoreError, require_staff
from stores.models import Store, StoreStaff, StoreSkuProfitRule
from inventory.pool_access import ensure_independent_pool
from wechat_integration.credentials import cipher
from .models import ProcurementOrder, ProcurementLine, ProcurementEvent, ProcurementProof, ProcurementSettings


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identifier(value):
    if not isinstance(value,str): raise StoreError('编号格式无效。')
    try:return uuid.UUID(value)
    except (ValueError,TypeError,AttributeError) as exc:raise StoreError('编号格式无效。') from exc


def text(value, limit=300, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise StoreError('请填写有效说明或凭证编号。')
    return value.strip()


def integer(value, maximum=9900000000, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise StoreError('金额、数量或修订号无效。')
    return value


def fields(body, allowed):
    if not isinstance(body, dict) or set(body) != set(allowed):
        raise StoreError('请求字段不正确。')


def lock_staff(member,store_id,permission):
    Member.objects.select_for_update().get(pk=member.pk)
    StoreStaff.objects.select_for_update().filter(store_id=store_id,member=member).first()
    return require_staff(member,store_id,permission)


def instructions(value):
    if not value:
        return ''
    try:
        return cipher().decrypt(value.encode()).decode()
    except Exception as exc:
        raise StoreError('采购收款说明暂不可用，请联系平台核对。', 'PROCUREMENT_SETTINGS_UNAVAILABLE', 503) from exc


def seal(value):
    try:return cipher().encrypt(value.encode()).decode()
    except Exception as exc:raise StoreError('采购加密配置暂不可用。','PROCUREMENT_SETTINGS_UNAVAILABLE',503) from exc


def data(row, admin=False, summary=False):
    lines = list(row.lines.all()) if 'lines' in getattr(row,'_prefetched_objects_cache',{}) else list(row.lines.select_related('sku'))
    changed = any(x.sku.current_unit_id != x.unit_version_id for x in lines)
    review_required = any(x.sku.current_unit_id != x.unit_version_id and x.reviewed_current_unit_id != x.sku.current_unit_id for x in lines)
    proof = None
    if row.proof_id:
        prefix = '/api/v1/admin/stores' if admin else f'/api/v1/app/store-center/stores/{row.store_id}'
        proof = {'assetId': str(row.proof_id), 'previewUrl': f'{prefix}/procurement/proofs/{row.proof_id}/file'}
    return {'id': str(row.pk), 'orderNo': 'JH'+row.pk.hex.upper(), 'storeId': str(row.store_id), 'storeName': row.store.name,
            'status': row.status, 'revision': row.revision, 'unitsChanged':changed,'unitReviewRequired':review_required, 'goodsAmountFen': row.goods_amount_fen, 'freightFen': row.freight_fen,
            'payableFen': row.goods_amount_fen+row.freight_fen, 'note': row.note, 'paymentProof': proof,
            'paymentInstructions': instructions(row.encrypted_instructions) if not summary and row.status not in ['REVIEW','QUOTE','CANCELLED'] else '',
            'shipment': {'carrier':row.carrier,'tracking':row.tracking} if row.tracking else None,
            'summaryLineCount':len(lines), 'lines': [{'id':str(x.pk),'skuId':str(x.sku_id),'productName':x.product_name,'specLabel':x.spec_label,
                       'saleUnit':x.sale_unit,'baseUnit':x.base_unit,'quantity':x.quantity,'purchaseCostFen':x.purchase_cost_fen,
                       'unitVersionId':str(x.unit_version_id),'conversionRatio':x.ratio} for x in (lines[:3] if summary else lines)],
            'records': [{'id':str(x.pk),'kind':x.kind,'note':x.note,'reference':x.reference,'proofAssetId':str(x.proof_id) if x.proof_id else None,
                'proofPreviewUrl':('/api/v1/admin/stores' if admin else f'/api/v1/app/store-center/stores/{row.store_id}')+f'/procurement/proofs/{x.proof_id}/file' if x.proof_id else None,
                'actorName':x.admin.login_name if x.admin_id else (x.member.nickname or x.member.member_no),'createdAt':x.created_at.isoformat()} for x in ([] if summary else row.events.select_related('admin','member'))],
            'createdAt':row.created_at.isoformat(), 'updatedAt':row.updated_at.isoformat()}


@transaction.atomic
def create_order(store, member, body):
    fields(body, ['requestKey','lines','note'])
    key = identifier(body['requestKey'])
    Member.objects.select_for_update().get(pk=member.pk)
    store = Store.objects.select_for_update().get(pk=store.pk)
    lock_staff(member, store.pk, 'procurement')
    existing = ProcurementOrder.objects.filter(request_key=key).first()
    if existing:
        if existing.store_id != store.pk or existing.member_id != member.pk or existing.request_digest != digest(body):
            raise StoreError('请求键已用于另一笔进货申请。','REQUEST_KEY_CONFLICT',409)
        return existing
    if not store.enabled or not store.warehouse.enabled:
        raise StoreError('前置仓已停用，不能新建进货单。','STORE_UNAVAILABLE',409)
    lines = body['lines']
    if not isinstance(lines,list) or not 1 <= len(lines) <= 100:
        raise StoreError('请选择1至100个规格。')
    snapshots, seen = [], set()
    for item in sorted(lines,key=lambda x:str(x.get('skuId','')) if isinstance(x,dict) else ''):
        fields(item,['skuId','quantity','expectedCostRevision','expectedUnitVersionId'])
        sku_id = identifier(item['skuId'])
        if sku_id in seen:
            raise StoreError('不能重复提交相同规格。')
        seen.add(sku_id)
        quantity = integer(item['quantity'],999,1)
        sku = Sku.objects.select_for_update(of=('self',)).select_related('current_unit','product').filter(pk=sku_id, product__fulfillment_kind='SHIP',product__status='ON_SALE',sale_status='ON_SALE').first()
        rule = StoreSkuProfitRule.objects.select_for_update().filter(sku_id=sku_id).first()
        if not sku or not sku.current_unit or not rule:
            raise StoreError('该规格暂不开放进货或未配置进货价。','PROCUREMENT_SKU_UNAVAILABLE',409)
        if rule.revision != integer(item['expectedCostRevision'],minimum=1) or sku.current_unit_id != identifier(item['expectedUnitVersionId']) or rule.unit_version_id != sku.current_unit_id:
            raise StoreError('进货价或销售单位已变化，请重新选择。','UNIT_REVISION_CONFLICT',409)
        pool = ensure_independent_pool(sku)
        snapshots.append(ProcurementLine(sku=sku,unit_version=sku.current_unit,anchor_sku_id=pool.anchor_sku_id,product_name=sku.product.name,spec_label=specification_label(sku),sale_unit=sku.current_unit.sale_unit,base_unit=sku.current_unit.base_unit,ratio=sku.current_unit.ratio,quantity=quantity,purchase_cost_fen=rule.purchase_cost_fen))
    row = ProcurementOrder.objects.create(store=store,member=member,request_key=key,request_digest=digest(body),goods_amount_fen=sum(x.quantity*x.purchase_cost_fen for x in snapshots),note=text(body['note']))
    ProcurementLine.objects.bulk_create([set_order(x,row) for x in snapshots])
    ProcurementEvent.objects.create(order=row,request_key=key,digest=digest(body),kind='submit',member=member,note=row.note)
    return row


def set_order(line,row):
    line.order = row
    return line


ACTION_FIELDS = {'confirm-quote':[], 'payment-proof':['assetId','note'], 'cancel':['reason'], 'exception':['note'], 'receive':['lineIds'],
                 'review':['freightFen'], 'reject':['reason'], 'confirm-payment':['paymentReference','note'], 'reject-proof':['reason'],
                 'ship':['carrier','tracking'], 'resolve-exception':['note','reference'], 'close-cancel':['reference','note','refundConfirmed'], 'verify-units':['note','reference']}
ALLOWED = {'confirm-quote':['QUOTE'],'payment-proof':['PAYMENT'],'cancel':['REVIEW','QUOTE','PAYMENT','PROOF','PAID','SHIPPED','EXCEPTION'],
           'exception':['SHIPPED','EXCEPTION'],'receive':['SHIPPED'], 'review':['REVIEW'],'reject':['REVIEW','QUOTE'],
           'confirm-payment':['PROOF'],'reject-proof':['PROOF'],'ship':['PAID'],'resolve-exception':['EXCEPTION'],'close-cancel':['CANCEL_REVIEW'],
           'verify-units':['REVIEW','QUOTE','PAYMENT','PROOF','PAID','SHIPPED','EXCEPTION']}


def check_units(row):
    for line in row.lines.select_related('sku'):
        if line.sku.current_unit_id != line.unit_version_id and line.reviewed_current_unit_id != line.sku.current_unit_id:
            raise StoreError('单位版本已变化，须平台核对本单冻结的原单位后再收货。','UNIT_REVISION_CONFLICT',409)


@transaction.atomic
def transition(row, actor, action, body, admin=False):
    if admin:
        from accounts.models import AdminAccount
        AdminAccount.objects.select_for_update().get(pk=actor.pk)
    else:
        Member.objects.select_for_update().get(pk=actor.pk)
    row = ProcurementOrder.objects.select_for_update().select_related('store','store__warehouse').get(pk=row.pk)
    if admin:
        from accounts.security import operation_permissions
        permission='stores.procurement.finance' if action in ['confirm-payment','close-cancel'] else 'stores.procurement.manage'
        if permission not in operation_permissions(actor): raise StoreError('没有采购操作权限。','STORE_FORBIDDEN',403)
    else:
        lock_staff(actor,row.store_id,'receiving' if action in ['receive','exception'] else 'procurement')
    if action not in ACTION_FIELDS:
        raise StoreError('进货操作不存在。')
    fields(body,['expectedRevision','requestKey',*ACTION_FIELDS[action]])
    key = identifier(body['requestKey'])
    integer(body['expectedRevision'], minimum=1)
    replay = ProcurementEvent.objects.filter(request_key=key).first()
    signature = digest({'action':action, 'body':body})
    if replay:
        if replay.order_id != row.pk or replay.digest != signature or (replay.admin_id if admin else replay.member_id) != actor.pk:
            raise StoreError('请求键与原操作不一致。','REQUEST_KEY_CONFLICT',409)
        return row
    if row.revision != body['expectedRevision']:
        raise StoreError('进货单已变化，请刷新后再试。','REVISION_CONFLICT',409)
    if row.status not in ALLOWED[action]:
        raise StoreError('当前状态不能执行此操作。','PROCUREMENT_STATE_CONFLICT',409)
    note, reference, proof = '', '', None
    if action == 'review':
        configuration = ProcurementSettings.objects.select_for_update().get(pk=1)
        if not instructions(configuration.encrypted_instructions):
            raise StoreError('请先配置采购线下收款说明。','PROCUREMENT_SETTINGS_REQUIRED',409)
        row.freight_fen = integer(body['freightFen'])
        row.encrypted_instructions = configuration.encrypted_instructions
        row.status = 'QUOTE'
    elif action == 'confirm-quote': row.status = 'PAYMENT'
    elif action == 'payment-proof':
        proof = ProcurementProof.objects.select_for_update().filter(pk=identifier(body['assetId']),store_id=row.store_id,member=actor).first()
        if not proof: raise StoreError('付款凭证不存在或不属于本仓本人。','PROCUREMENT_PROOF_FORBIDDEN',403)
        row.proof = proof; note = text(body['note']); row.status='PROOF'
    elif action in ['cancel','reject']:
        note = text(body['reason'], required=True)
        row.status = 'CANCELLED' if row.status in ['REVIEW','QUOTE'] else 'CANCEL_REVIEW'
    elif action == 'confirm-payment':
        reference=text(body['paymentReference'],120,True); note=text(body['note']); row.status='PAID'
    elif action == 'reject-proof': note=text(body['reason'],required=True); row.status='PAYMENT'; row.proof=None
    elif action == 'ship': row.carrier=text(body['carrier'],80,True); row.tracking=text(body['tracking'],120,True); row.status='SHIPPED'; reference=row.tracking
    elif action == 'exception': note=text(body['note'],required=True); row.status='EXCEPTION'
    elif action == 'resolve-exception': note=text(body['note'],required=True); reference=text(body['reference'],120,True); row.status='SHIPPED'
    elif action == 'close-cancel':
        if body['refundConfirmed'] is not True: raise StoreError('须核清款项并完成所需线下退款。')
        note=text(body['note'],required=True); reference=text(body['reference'],120,True); row.status='CANCELLED'
    elif action == 'verify-units':
        note=text(body['note'],required=True); reference=text(body['reference'],120,True)
        locked = {x.pk:x for x in Sku.objects.select_for_update(of=('self',)).select_related('current_unit').filter(pk__in=row.lines.values_list('sku_id',flat=True)).order_by('id')}
        from inventory.pool_access import resolve_anchor_ids
        anchors=resolve_anchor_ids(locked)
        for line in row.lines.select_related('sku'):
            sku=locked[line.sku_id]
            if not sku.current_unit or anchors.get(sku.pk)!=line.anchor_sku_id or sku.current_unit.base_unit!=line.base_unit: raise StoreError('基础单位或库存池变化，不能批准自动入库。','PROCUREMENT_POOL_CONFLICT',409)
            line.unit_reviewed=True;line.reviewed_current_unit_id=sku.current_unit_id
            line.save(update_fields=['unit_reviewed','reviewed_current_unit_id'])
    elif action == 'receive':
        ids=body['lineIds']
        if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids) or len(ids)!=len(set(ids)) or set(ids)!={str(x.pk) for x in row.lines.all()}: raise StoreError('请逐行确认整单全部实际验收合格。')
        check_units(row)
        from inventory.procurement import receive_procurement
        receive_procurement(row,actor)
        row.status='RECEIVED'
    row.revision += 1
    row.save()
    ProcurementEvent.objects.create(order=row,request_key=key,digest=signature,kind=action,note=note,reference=reference,proof=proof,admin=actor if admin else None,member=None if admin else actor)
    return row
