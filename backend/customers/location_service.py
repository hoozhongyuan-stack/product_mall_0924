"""Server-owned map lookup. Coordinates use AMap/WeChat GCJ-02 throughout."""
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import URLError
from django.views.decorators.debug import sensitive_variables
from stores.map_configuration import effective_configuration
from wechat_integration.credentials import CredentialsUnavailable


class LocationError(ValueError):
    def __init__(self, message, code='MAP_UNAVAILABLE', status=502):
        super().__init__(message)
        self.code, self.status = code, status


def coordinates(latitude, longitude):
    if (latitude is None) != (longitude is None):
        raise ValueError('请重新选择完整的收货位置。')
    values = []
    for value, limit in ((latitude, 90), (longitude, 180)):
        try:
            number = Decimal(str(value)) if value is not None else None
            if number is not None and (not number.is_finite() or abs(number) > limit):
                raise ValueError('收货位置无效，请重新选择地图位置。')
            values.append(number.quantize(Decimal('0.000001')) if number is not None else None)
        except (InvalidOperation, TypeError) as exc:
            raise ValueError('收货位置无效，请重新选择地图位置。') from exc
    return tuple(values)


@sensitive_variables('key', 'params', 'upstream')
def lookup(path, arguments):
    try:
        key = effective_configuration()['webServiceKey']
    except CredentialsUnavailable as exc:
        raise LocationError('地图配置暂不可用，请稍后重试。', 'MAP_CONFIGURATION_UNAVAILABLE', 503) from exc
    if not key:
        raise LocationError('地图服务尚未配置，请联系平台。', 'MAP_NOT_CONFIGURED', 503)
    params = urlencode({**arguments, 'key': key})
    try:
        with urlopen('https://restapi.amap.com/v3/' + path + '?' + params, timeout=5) as upstream:
            raw = upstream.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('status') != '1':
            raise ValueError()
        return result
    except (URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
        raise LocationError('地图暂不可用，请重试；已填写的收货信息会保留。') from exc


def text(value):
    return value.strip() if isinstance(value, str) else ''


def poi(row):
    if not isinstance(row, dict) or not isinstance(row.get('location'), str):
        return None
    try:
        lng, lat = row['location'].split(',')
        latitude, longitude = coordinates(lat, lng)
    except ValueError:
        return None
    result = {'poiName': text(row.get('name')), 'locationAddress': text(row.get('address')),
              'province': text(row.get('pname')), 'city': text(row.get('cityname')) or text(row.get('pname')),
              'district': text(row.get('adname')), 'latitude': str(latitude), 'longitude': str(longitude)}
    return result if all(result.values()) else None


def search(query, city):
    result = lookup('place/text', {'keywords': query, 'city': city, 'citylimit': 'true' if city else 'false',
                                 'offset': 10, 'page': 1, 'extensions': 'base'})
    rows = result.get('pois')
    if not isinstance(rows, list):
        raise LocationError('地图返回异常，请重试。')
    return [item for row in rows[:10] if (item := poi(row))]


def reverse(latitude, longitude):
    result = lookup('geocode/regeo', {'location': f'{longitude},{latitude}', 'extensions': 'all', 'radius': 100})
    geo = result.get('regeocode')
    if not isinstance(geo, dict) or not isinstance(geo.get('addressComponent'), dict):
        raise LocationError('无法识别该位置，请搜索小区或大厦名称。')
    component = geo['addressComponent']
    formatted = text(geo.get('formatted_address'))
    item = {'poiName': formatted, 'locationAddress': formatted,
            'province': text(component.get('province')), 'city': text(component.get('city')) or text(component.get('province')),
            'district': text(component.get('district')), 'latitude': str(latitude), 'longitude': str(longitude)}
    if not all(item.values()):
        raise LocationError('无法识别完整地址，请搜索小区或大厦名称。')
    return item
