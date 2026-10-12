"""Administrative selectors from configured AMap district service, never invented codes."""
import json
import re
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler
from django.core.cache import cache
from stores.map_configuration import effective_configuration
from wechat_integration.credentials import CredentialsUnavailable
from django.views.decorators.debug import sensitive_variables


class RegionUnavailable(ValueError):
    pass


@sensitive_variables('key','url','upstream')
def region_children(query):
    if set(query)-{'parentAdcode','keywords','level'} or any(len(query.getlist(k))!=1 for k in query):
        raise ValueError('地区检索参数无效。')
    parent=query.get('parentAdcode','')
    keywords=query.get('keywords','中国')
    if parent and not re.fullmatch(r'\d{6}',parent):raise ValueError('地区代码无效。')
    if keywords not in ('中国','100000'):raise ValueError('地区选择须从中国开始逐级选择。')
    if parent and parent!='100000' and not cache.get('shipping-verified-region:'+parent):
        raise RegionUnavailable('请选择上级地区后展开下级地区。')
    cache_key='shipping-regions:'+ (parent or keywords)
    found=cache.get(cache_key)
    if found is not None:return found
    try:key=effective_configuration()['webServiceKey']
    except CredentialsUnavailable as exc:raise RegionUnavailable('地图凭据不可用，请检查地图配置。') from exc
    if not key:raise RegionUnavailable('请先配置高德地图服务密钥以选择省市区。')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    url='https://restapi.amap.com/v3/config/district?'+urlencode(dict(key=key,keywords=parent or keywords,subdistrict=1,extensions='base'))
    try:
        with build_opener(NoRedirect()).open(url,timeout=5) as upstream:raw=upstream.read(524289)
        if len(raw)>524288:raise ValueError()
        result=json.loads(raw)
        if result.get('status')!='1' or not isinstance(result.get('districts'),list):raise ValueError()
        roots=result['districts']
        if len(roots)!=1 or str(roots[0].get('adcode'))!=(parent or '100000'):raise ValueError()
        rows=roots[0].get('districts',[])
        items=[]
        parent_record=cache.get('shipping-verified-region:'+parent) if parent else None
        ancestors=(parent_record.get('ancestors',[])+[{key:parent_record[key] for key in ('code','name','level')}]) if parent_record else []
        for row in rows:
            if row.get('level') not in ['province','city','district'] or not re.fullmatch(r'\d{6}',str(row.get('adcode',''))):continue
            items.append(dict(adcode=row['adcode'],code=row['adcode'],name=row['name'],level=row['level'],parentAdcode=parent or roots[0].get('adcode','100000'),ancestors=ancestors))
        items=sorted(items,key=lambda row:row['code'])
        for item in items:cache.set('shipping-verified-region:'+item['code'],item,86400)
        cache.set(cache_key,items,3600)
        return items
    except (OSError,ValueError,TypeError,KeyError) as exc:
        raise RegionUnavailable('地区服务暂时不可用，请重试。') from exc
