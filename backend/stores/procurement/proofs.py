"""Private member-owned receipts; never stored in the public catalog library."""
import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_control
from catalog.media import inspect_file, verify_decodable, serve_asset
from catalog.service import CatalogError
from customers.auth import require_member
from customers.models import Member
from accounts.security import require
from common.http import response, method
from stores.views import guarded
from stores.access import StoreError, require_staff
from .models import ProcurementProof, ProcurementOrder, ProcurementEvent
from .views import member_scope
from .service import lock_staff


MAX_BYTES = 5 * 1024 * 1024


@csrf_exempt
@cache_control(private=True,no_store=True)
@guarded
def upload(request,store_id):
    bad=method(request,'POST')
    if bad:return bad
    member,bad=require_member(request)
    if bad:return bad
    require_staff(member,store_id,'procurement')
    if int(request.META.get('CONTENT_LENGTH') or 0)>MAX_BYTES+65536 or set(request.FILES)!={'file'} or request.POST or request.content_type!='multipart/form-data':
        raise StoreError('请上传单张PNG或JPEG付款凭证，最大5MB。')
    source=request.FILES['file']
    if not 0<source.size<=MAX_BYTES:raise StoreError('付款凭证最大5MB。')
    root=Path(settings.MEDIA_ROOT)/'product'/'procurement-proofs'
    temporary=final=None
    try:
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with tempfile.NamedTemporaryFile(dir=root,suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);size=0;checksum=hashlib.sha256()
            for chunk in source.chunks():
                size+=len(chunk)
                if size>MAX_BYTES:raise StoreError('付款凭证最大5MB。')
                stream.write(chunk);checksum.update(chunk)
        content_type,extension,_,_=inspect_file('IMAGE',temporary)
        verify_decodable(temporary,'IMAGE')
        proof_id=uuid.uuid4();relative=f'product/procurement-proofs/{proof_id}.{extension}'
        final=Path(settings.MEDIA_ROOT)/relative
        with transaction.atomic():
            Member.objects.select_for_update().get(pk=member.pk)
            lock_staff(member,store_id,'procurement')
            unbound=ProcurementProof.objects.exclude(pk__in=ProcurementOrder.objects.exclude(proof_id=None).values('proof_id')).exclude(pk__in=ProcurementEvent.objects.exclude(proof_id=None).values('proof_id'))
            if unbound.filter(member=member).count()>=100:
                raise StoreError('未绑定付款凭证已达上限，请先提交已有凭证或联系平台处理。','MEDIA_QUOTA',409)
            os.replace(temporary,final)
            proof=ProcurementProof.objects.create(id=proof_id,store_id=store_id,member=member,stored_name=relative,content_type=content_type,byte_size=size,sha256=checksum.hexdigest())
        final=None
        return response(request,{'assetId':str(proof.pk),'previewUrl':f'/api/v1/app/store-center/stores/{store_id}/procurement/proofs/{proof.pk}/file'},201)
    except CatalogError as exc:
        raise StoreError(str(exc),exc.code,exc.status) from exc
    except OSError as exc:
        raise StoreError('付款凭证存储暂不可用，请稍后重试。','MEDIA_STORAGE_UNAVAILABLE',503) from exc
    finally:
        if temporary:temporary.unlink(missing_ok=True)
        if final:final.unlink(missing_ok=True)


@csrf_exempt
@cache_control(private=True,no_store=True)
@guarded
def file(request,proof_id,store_id=None):
    bad=method(request,'GET')
    if bad:return bad
    proof=ProcurementProof.objects.filter(pk=proof_id).first()
    if not proof:raise StoreError('凭证不存在。','PROCUREMENT_PROOF_NOT_FOUND',404)
    bound=ProcurementOrder.objects.filter(Q(proof_id=proof_id)|Q(events__proof_id=proof_id)).distinct()
    if store_id:
        member,bad=require_member(request)
        if bad:return bad
        member_scope(member,store_id)
        if proof.store_id!=store_id or (proof.member_id!=member.pk and not bound.filter(store_id=store_id).exists()):
            raise StoreError('没有本付款凭证的查看权限。','PROCUREMENT_PROOF_FORBIDDEN',403)
    else:
        _,bad=require(request,'stores.procurement.read')
        if bad:return bad
        if not bound.exists():raise StoreError('未绑定单据的凭证不能由平台读取。','PROCUREMENT_PROOF_FORBIDDEN',403)
    asset=SimpleNamespace(kind='IMAGE',stored_name=proof.stored_name,content_type=proof.content_type)
    try:return serve_asset(request,asset)
    except CatalogError as exc:raise StoreError(str(exc),exc.code,exc.status) from exc
