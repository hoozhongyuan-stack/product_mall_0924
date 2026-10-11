"""Generate actual WeChat mini-program table codes; never fabricate a QR."""
import json
import ssl
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler
from urllib.error import HTTPError, URLError
from django.http import HttpResponse
from django.conf import settings
from accounts.security import require
from common.http import method
from .access import DiningError, get_restaurant
from .models import DiningTable
from .views import guarded


def generate_code(code):
    version = settings.DINING_CODE_ENV_VERSION
    check_path = settings.DINING_CODE_CHECK_PATH
    if not isinstance(version,str) or version not in {'release','trial','develop'} or type(check_path) is not bool:
        raise DiningError('餐桌码目标版本或页面校验配置无效，请检查部署配置。','DINING_CODE_CONFIG_INVALID',503)
    from wechat_integration.credentials import effective_credentials, CredentialsUnavailable
    from wechat_integration.probe import acquire_access_token, NoRedirect
    try:
        credentials=effective_credentials()
        if not credentials.app_id or not credentials.secret:
            raise DiningError('尚未配置有效小程序凭据，无法生成餐桌码。','DINING_CODE_NOT_CONFIGURED',409)
        token=acquire_access_token(credentials)
    except CredentialsUnavailable:
        raise DiningError('小程序凭据不可用，请检查配置。','DINING_CODE_NOT_CONFIGURED',409) from None
    if token.status!='SUCCESS':
        raise DiningError('微信凭据验证失败，无法生成餐桌码。','DINING_CODE_UNAVAILABLE',502)
    url='https://api.weixin.qq.com/wxa/getwxacodeunlimit?'+urlencode({'access_token':token.token})
    payload={'scene':code,'page':'pages/dining/shop','env_version':version,'check_path':check_path,'width':430}
    request=Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    try:
        opener=build_opener(ProxyHandler({}),HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
        with opener.open(request,timeout=8) as result:
            raw=result.read(1024*1024+1)
        if len(raw)>1024*1024 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise DiningError('微信未返回有效餐桌码，请确认目标小程序版本和餐饮页面配置。','DINING_CODE_UNAVAILABLE',502)
        return raw
    except (HTTPError,URLError,OSError,TimeoutError):
        raise DiningError('微信餐桌码服务暂不可用，请稍后重试。','DINING_CODE_UNAVAILABLE',502) from None


@guarded
def table_code_view(request,restaurant_id,table_id):
    bad=method(request,'GET')
    if bad:return bad
    _,bad=require(request,'dining.manage')
    if bad:return bad
    restaurant=get_restaurant(restaurant_id)
    table=DiningTable.objects.filter(pk=table_id,restaurant=restaurant,enabled=True).first()
    if table is None or not restaurant.enabled:
        raise DiningError('餐桌码不可用。','DINING_NOT_FOUND',404)
    raw=generate_code(table.code)
    result=HttpResponse(raw,content_type='image/png')
    result['Content-Disposition']=f'attachment; filename="dining-table-{table.id}.png"'
    return result
