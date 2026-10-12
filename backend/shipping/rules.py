"""Pure integer-fen pricing and GCJ-02 geofence operations."""
import math


class ShippingRuleError(ValueError):
    pass


def integer(value, minimum=0, maximum=1_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ShippingRuleError('金额、数量或距离参数无效。')
    return value


def distance_meters(a, b):
    lat1, lat2 = math.radians(float(a['latitude'])), math.radians(float(b['latitude']))
    delta = math.radians(float(a['longitude']) - float(b['longitude']))
    value = math.sin((lat1-lat2)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(delta/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, value))))


def point_in_polygon(point, vertices):
    x, y = float(point['longitude']), float(point['latitude'])
    inside = False
    for index, vertex in enumerate(vertices):
        other = vertices[index-1]
        ax, ay = float(vertex['longitude']), float(vertex['latitude'])
        bx, by = float(other['longitude']), float(other['latitude'])
        cross = (x-ax)*(by-ay)-(y-ay)*(bx-ax)
        if abs(cross) < 1e-10 and min(ax,bx)-1e-10 <= x <= max(ax,bx)+1e-10 and min(ay,by)-1e-10 <= y <= max(ay,by)+1e-10:
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def within(zone, point):
    if zone['kind'] == 'CIRCLE':
        return distance_meters(zone['center'], point) <= zone['radiusMeters'] + .01
    return point_in_polygon(point, zone['polygon'])


def in_time(start, end, current):
    return start <= current <= end if start <= end else current >= start or current <= end


def delivery_zone(zones, point, current_time):
    matching = [row for row in zones if row['enabled'] and within(row, point)]
    if any(row['kind'] == 'EXCLUDE' for row in matching):
        raise ShippingRuleError('收货地址位于禁止配送区域，请更换地址或交付方式。')
    service = sorted([row for row in matching if row['kind'] != 'EXCLUDE'], key=lambda row: (row['priority'], row['id']))
    if not service:
        raise ShippingRuleError('收货地址超出配送范围，请更换地址或交付方式。')
    chosen = service[0]
    if not in_time(chosen['startTime'], chosen['endTime'], current_time):
        raise ShippingRuleError('当前不在该区域配送时段内，请选择其他交付方式。')
    return chosen


def courier_cost(rule, pieces, grams):
    if rule['mode'] == 'FIXED':
        return rule['firstFeeFen']
    units = pieces if rule['mode'] == 'PIECE' else grams
    if units is None or units <= 0:
        raise ShippingRuleError('商品销售单位重量尚未配置，请联系平台或选择其他交付方式。')
    extra = max(0, units-rule['firstUnits'])
    return rule['firstFeeFen'] + ((extra+rule['extraUnits']-1)//rule['extraUnits'])*rule['extraFeeFen']


def delivery_cost(rule, distance):
    if rule['feeMode'] == 'FIXED':
        return rule['feeFen']
    extra = max(0, int(math.ceil(distance))-rule['firstMeters'])
    return rule['feeFen'] + ((extra+rule['extraMeters']-1)//rule['extraMeters'])*rule['extraFeeFen']


def apply_funding(cost, goods_amount, free_threshold, platform_free):
    source = 'PLATFORM' if platform_free else 'WAREHOUSE' if free_threshold is not None and goods_amount >= free_threshold else 'NONE'
    return dict(costFen=cost, customerFeeFen=cost if source == 'NONE' else 0,
                warehouseBearsFen=cost if source == 'WAREHOUSE' else 0,
                platformBearsFen=cost if source == 'PLATFORM' else 0, freeSource=source)
