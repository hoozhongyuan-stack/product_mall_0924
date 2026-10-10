"""AMap configuration: masked reads and purpose-bound authenticated encryption."""
from django.conf import settings
from django.db import transaction
from django.views.decorators.cache import cache_control
from django.views.decorators.debug import sensitive_variables
from accounts.models import AdminAccount
from accounts.security import audit, confirm_action, require, require_live
from common.http import error, method, parse_json_object, response
from wechat_integration.credentials import CredentialsUnavailable, key_available
from wechat_open_platform.crypto import seal, unseal
from .access import StoreError
from .models import StoreMapConfiguration
from .views import guarded

FIELDS = ('webServiceKey','jsApiKey','jsSecurityCode')
PURPOSE = 'stores-amap-credentials'


def configuration_row(lock=False):
    query = StoreMapConfiguration.objects.select_for_update() if lock else StoreMapConfiguration.objects
    return query.get(pk=1)


@sensitive_variables('payload')
def effective_configuration(row=None):
    row = configuration_row() if row is None else row
    if not row.managed:
        return dict(zip(FIELDS, (getattr(settings,'AMAP_WEB_SERVICE_KEY',''),
                    getattr(settings,'AMAP_JS_API_KEY',''),getattr(settings,'AMAP_JS_SECURITY_CODE',''))))
    payload = unseal(PURPOSE,row.encrypted_payload)
    if not isinstance(payload,dict) or set(payload)!=set(FIELDS) or any(not isinstance(payload[key],str) for key in FIELDS):
        raise CredentialsUnavailable()
    return payload


def configuration_data(row=None):
    row = configuration_row() if row is None else row
    try:
        values = effective_configuration(row)
        available = True
    except CredentialsUnavailable:
        values = dict.fromkeys(FIELDS,'')
        available = False
    return {'revision':row.revision,'managed':row.managed,'source':'MANAGED' if row.managed else 'ENV',
            'encryptionReady':key_available(),'available':available,
            **{key:{'configured':bool(values[key]),'tail':values[key][-4:] if values[key] else ''} for key in FIELDS}}


@sensitive_variables('body','current','values')
def replacement(row,body):
    if set(body)!=set(FIELDS)|{'expectedRevision'} or type(body['expectedRevision']) is not int or body['expectedRevision']<0:
        raise StoreError('地图配置参数无效。')
    if any(not isinstance(body[key],str) or len(body[key])>256 or any(char.isspace() for char in body[key]) for key in FIELDS):
        raise StoreError('密钥格式无效，请检查输入。')
    try:
        current=effective_configuration(row)
    except CredentialsUnavailable:
        if not body['webServiceKey'] or bool(body['jsApiKey']) != bool(body['jsSecurityCode']):
            raise CredentialsUnavailable() from None
        current=dict.fromkeys(FIELDS,'')
    values={key:body[key] or current[key] for key in FIELDS}
    if not values['webServiceKey'] or bool(values['jsApiKey']) != bool(values['jsSecurityCode']):
        raise StoreError('请配置 Web 服务密钥；Web 端 JS API 密钥与安全密钥须成对配置。')
    return values


@cache_control(private=True,no_store=True)
@guarded
@sensitive_variables('body','values')
def map_settings(request):
    bad=method(request,'GET','PUT')
    if bad:return bad
    actor,bad=require(request,'stores.manage')
    if bad:return bad
    if request.method=='GET':return response(request,configuration_data())
    try:
        body=parse_json_object(request,max_bytes=4096)
        with transaction.atomic():
            AdminAccount.objects.select_for_update().get(pk=actor.pk)
            actor,bad=require_live(request,'stores.manage')
            if bad:return bad
            row=configuration_row(lock=True)
            values=replacement(row,body)
            if body['expectedRevision']!=row.revision:
                return error(request,409,'REVISION_CONFLICT','配置已变化，请刷新后重试。')
            bad=confirm_action(request,actor,'stores.map.configure','amap',row.revision)
            if bad:return bad
            encrypted=seal(PURPOSE,values)
            before=row.revision
            row.encrypted_payload=encrypted
            row.managed=True
            row.revision+=1
            row.save(update_fields=['encrypted_payload','managed','revision','updated_at'])
            audit(request,'stores.map.configure','store_map_configuration','amap',actor,
                  before={'revision':before},after={'revision':row.revision,'managed':True})
        return response(request,configuration_data(row))
    except CredentialsUnavailable:
        return error(request,503,'MAP_CONFIGURATION_UNAVAILABLE','地图凭据不可用，请检查持久化加密密钥或重新配置。')
