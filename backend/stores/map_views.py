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
from django.views.decorators.cache import cache_control
from django.views.decorators.vary import vary_on_headers


@cache_control(private=True, no_store=True)
@vary_on_headers('Cookie')
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
    if set(request.GET) - {'q', 'city'} or any(len(request.GET.getlist(k)) != 1 for k in request.GET):
        raise StoreError('地图搜索参数无效。')
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


from django.http import HttpResponse
from customers.location_service import coordinates, reverse, LocationError


def map_item(item):
    return {'name': item['poiName'], 'address': item['locationAddress'], 'city': item['city'],
            'latitude': float(item['latitude']), 'longitude': float(item['longitude'])}


@cache_control(private=True, no_store=True)
@vary_on_headers('Cookie')
@guarded
def map_reverse(request):
    bad = method(request, 'GET')
    if bad: return bad
    actor, bad = require(request, 'stores.manage')
    if bad: return bad
    if set(request.GET) != {'latitude', 'longitude'} or any(len(request.GET.getlist(k)) != 1 for k in request.GET):
        raise StoreError('请提供完整的地图坐标。')
    try:
        lat, lng = coordinates(request.GET['latitude'], request.GET['longitude'])
        if lat is None: raise ValueError()
        return response(request, map_item(reverse(lat, lng)))
    except LocationError as exc:
        return error(request, exc.status, exc.code, str(exc))


@cache_control(private=True, no_store=True)
@vary_on_headers('Cookie')
@guarded
@sensitive_variables('values')
def map_runtime(request):
    bad = method(request, 'GET')
    if bad: return bad
    actor, bad = require(request, 'stores.manage')
    if bad: return bad
    try:
        values = effective_configuration()
    except CredentialsUnavailable:
        return error(request, 503, 'MAP_CONFIGURATION_UNAVAILABLE', '地图配置不可用，请检查后台配置。')
    if not values['jsApiKey'] or not values['jsSecurityCode']:
        return error(request, 503, 'MAP_NOT_CONFIGURED', '请在前置仓设置配置高德 JS API 密钥和安全密钥。')
    return response(request, {'jsApiKey': values['jsApiKey'],
        'securityProxyUrl': '/api/v1/admin/stores/amap-proxy/_AMapService'})


PROXY_TARGETS = {'v4/map/styles': 'https://webapi.amap.com/v4/map/styles',
                 'v3/place/text': 'https://restapi.amap.com/v3/place/text',
                 'v3/geocode/regeo': 'https://restapi.amap.com/v3/geocode/regeo'}


@cache_control(private=True, no_store=True)
@vary_on_headers('Cookie')
@guarded
@sensitive_variables('values', 'arguments', 'url', 'upstream')
def amap_proxy(request, service_path):
    """Only known SDK services; no arbitrary URLs, redirects or client credentials."""
    bad = method(request, 'GET')
    if bad: return bad
    actor, bad = require(request, 'stores.manage')
    if bad: return bad
    target = PROXY_TARGETS.get(service_path)
    if target is None:
        return error(request, 404, 'NOT_FOUND', '地图服务不可用。')
    if len(request.META.get('QUERY_STRING', '')) > 4096 or any(len(request.GET.getlist(k)) != 1 for k in request.GET):
        raise StoreError('地图查询参数无效。')
    try:
        values = effective_configuration()
        if not values['jsApiKey'] or not values['jsSecurityCode']:
            return error(request, 503, 'MAP_NOT_CONFIGURED', '地图服务尚未配置。')
        arguments = {key: value for key, value in request.GET.items() if key not in ('key', 'jscode')}
        arguments.update(key=values['jsApiKey'], jscode=values['jsSecurityCode'])
        # No redirects: credentials must never leave the fixed AMap endpoint.
        from urllib.request import build_opener, HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        url = target + '?' + urlencode(arguments)
        with build_opener(NoRedirect()).open(url, timeout=5) as upstream:
            raw = upstream.read(1048577)
            content_type = upstream.headers.get('Content-Type', '').split(';')[0]
        if values['jsSecurityCode'].encode() in raw:
            raise ValueError()
        if len(raw) > 1048576 or content_type not in ('application/json', 'text/plain', 'application/octet-stream'):
            raise ValueError()
        result = HttpResponse(raw, content_type=content_type)
        result['X-Content-Type-Options'] = 'nosniff'
        return result
    except CredentialsUnavailable:
        return error(request, 503, 'MAP_CONFIGURATION_UNAVAILABLE', '地图配置不可用。')
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        return error(request, 502, 'MAP_UNAVAILABLE', '地图加载失败，请重试。')
