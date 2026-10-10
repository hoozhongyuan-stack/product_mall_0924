"""AMap search is server-owned and returns only validated POI coordinates."""
import json
import math
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError
from wechat_integration.credentials import CredentialsUnavailable
from .map_configuration import effective_configuration
from common.http import response,error,method
from django.views.decorators.debug import sensitive_variables
from accounts.security import require
from .views import guarded
from .access import StoreError


@guarded
@sensitive_variables("web_key", "params", "upstream")
def map_search(request):
    bad=method(request,'GET')
    if bad:return bad
    actor,bad=require(request,'stores.manage')
    if bad:return bad
    try:
        web_key = effective_configuration()['webServiceKey']
    except CredentialsUnavailable:
        return error(request,503,'MAP_CONFIGURATION_UNAVAILABLE','地图凭据不可用，请检查加密密钥与后台配置。')
    if not web_key:
        return error(request,503,'MAP_NOT_CONFIGURED','高德地图服务密钥尚未配置，可先填写地址和经纬度。')
    query=request.GET.get('q','').strip()
    city=request.GET.get('city','').strip()
    if not 2<=len(query)<=120 or len(city)>80:raise StoreError('请填写 2 至 120 字的地点名称。')
    params=urlencode({'key':web_key,'keywords':query,'city':city,'offset':10,'page':1,'extensions':'base'})
    try:
        with urlopen('https://restapi.amap.com/v3/place/text?'+params,timeout=5) as upstream:
            raw=upstream.read(65537)
        if len(raw)>65536:raise ValueError()
        result=json.loads(raw)
        if not isinstance(result,dict) or not isinstance(result.get('pois'),list):raise ValueError()
        if result.get('status')!='1':raise ValueError()
        items=[]
        for row in result.get('pois',[])[:10]:
            if not isinstance(row,dict) or not isinstance(row.get('location'),str):raise ValueError()
            lng,lat=map(float,row['location'].split(','))
            if not math.isfinite(lng) or not math.isfinite(lat) or abs(lng)>180 or abs(lat)>90:continue
            items.append({'name':row['name'],'address':''.join(str(row.get(key,'')) for key in ('pname','cityname','adname','address')),'city':row.get('cityname',''),'latitude':lat,'longitude':lng})
        return response(request,{'configured':True,'items':items})
    except (URLError,TimeoutError,OSError,ValueError,KeyError,TypeError):
        return error(request,502,'MAP_UNAVAILABLE','地图搜索暂时不可用，请重试或手动填写位置。')
