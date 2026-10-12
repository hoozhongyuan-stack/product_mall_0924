/** Pure HTML-prototype helpers. Cost allocation is illustrative, not a settlement model. */
function finite(value, name, minimum = 0) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum) {
    throw new TypeError(`${name}必须是大于或等于${minimum}的有限数字`);
  }
  return value;
}

function integer(value, name, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  finite(value, name, minimum);
  if (!Number.isSafeInteger(value) || value > maximum) throw new RangeError(`${name}超出整数范围`);
  return value;
}

function positive(value, name) {
  finite(value, name);
  if (value === 0) throw new RangeError(`${name}必须大于0`);
  return value;
}

function boolean(value, name) {
  if (typeof value !== 'boolean') throw new TypeError(`${name}必须是布尔值`);
  return value;
}

export function money(fen) {
  integer(fen, '金额');
  return `¥${(fen / 100).toFixed(2)}`;
}

export function quantity(value) {
  const parsed = typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : value;
  return integer(parsed, '数量', 0, 999);
}

export function businessNumber(type, date, sequence) {
  if (typeof type !== 'string' || !/^[A-Z]{2}$/.test(type)) throw new TypeError('类型码必须为两个大写字母');
  integer(sequence, '当日流水', 1, 999999);
  if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(date)) {
    throw new TypeError('创建时间必须是带时区的 ISO 时间');
  }
  const instant = new Date(date);
  if (!Number.isFinite(instant.getTime())) throw new TypeError('创建时间无效');
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant);
  const fields = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${type}${fields.year}${fields.month}${fields.day}${fields.hour}${fields.minute}${fields.second}${String(sequence).padStart(6, '0')}`;
}

function splitCost(costFen, amount, freeThreshold, campaign) {
  integer(costFen, '示例配送成本');
  if (campaign) return { feeFen: 0, costFen, storeBearsFen: 0, platformBearsFen: costFen, reason: '平台活动包邮，平台承担' };
  if (freeThreshold > 0 && amount >= freeThreshold) {
    return { feeFen: 0, costFen, storeBearsFen: costFen, platformBearsFen: 0, reason: '经营包邮，前置仓承担' };
  }
  return { feeFen: costFen, costFen, storeBearsFen: 0, platformBearsFen: 0, reason: '顾客支付运费' };
}

function steppedCost(firstAmount, firstUnits, extraAmount, extraUnits, units) {
  const extraCount = Math.max(0, Math.ceil((units - firstUnits) / extraUnits));
  return integer(firstAmount + extraCount * extraAmount, '示例配送成本');
}

/** amount is eligible merchandise after coupons, before points redemption. Units for weight are grams. */
export function quoteFreight(config, basket) {
  if (!['fixed', 'piece', 'weight'].includes(config.mode)) throw new TypeError('快递计费方式无效');
  integer(config.firstAmount, '首费');
  integer(config.extraAmount, '续费');
  positive(config.firstUnits, '首计费单位');
  positive(config.extraUnits, '续计费单位');
  integer(config.freeThreshold, '包邮门槛');
  boolean(config.blocked, '禁配状态');
  integer(basket.amount, '门槛商品金额');
  integer(basket.pieces, '计费件数', 1);
  positive(basket.grams, '计费重量');
  boolean(basket.campaign, '平台活动命中');
  if (config.blocked) return { feeFen: 0, costFen: 0, storeBearsFen: 0, platformBearsFen: 0, reason: '该地区不支持快递配送' };
  const units = config.mode === 'piece' ? basket.pieces : basket.grams;
  const cost = config.mode === 'fixed' ? config.firstAmount : steppedCost(config.firstAmount, config.firstUnits, config.extraAmount, config.extraUnits, units);
  return splitCost(cost, basket.amount, config.freeThreshold, basket.campaign);
}

function validatePoint(point) {
  // Coordinates are planar SVG preview positions, never longitude/latitude.
  finite(point?.x, '横坐标', -Number.MAX_VALUE);
  finite(point?.y, '纵坐标', -Number.MAX_VALUE);
}

export function pointInPolygon(point, polygon) {
  validatePoint(point);
  if (!Array.isArray(polygon) || polygon.length < 3) throw new TypeError('多边形至少需要三个点');
  polygon.forEach(validatePoint);
  const area = polygon.reduce((sum, a, i) => {
    const b = polygon[(i + 1) % polygon.length];
    return sum + a.x * b.y - b.x * a.y;
  }, 0);
  if (!Number.isFinite(area) || Math.abs(area) < 1e-8) throw new TypeError('多边形需要有效面积');
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[j];
    const b = polygon[i];
    const cross = (point.x - a.x) * (b.y - a.y) - (point.y - a.y) * (b.x - a.x);
    const onSegment = Math.abs(cross) < 1e-8 && point.x >= Math.min(a.x, b.x) && point.x <= Math.max(a.x, b.x)
      && point.y >= Math.min(a.y, b.y) && point.y <= Math.max(a.y, b.y);
    if (onSegment) return true;
    if ((a.y > point.y) !== (b.y > point.y) && point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}

export function inCircle(point, circle) {
  validatePoint(point);
  validatePoint(circle);
  positive(circle.radius, '围栏半径');
  return Math.hypot(point.x - circle.x, point.y - circle.y) <= circle.radius;
}

function denied(eligibility, reason) {
  return { feeFen: 0, costFen: 0, storeBearsFen: 0, platformBearsFen: 0, reason, eligible: false, eligibility };
}

/** Pure preview: distance is supplied straight-line km; does not query a real map or settlement ledger. */
export function zoneQuote(zone, context) {
  if (!['fixed', 'distance'].includes(zone.mode)) throw new TypeError('配送区域计费方式无效');
  integer(zone.fixedAmount, '区域固定费');
  integer(zone.firstAmount, '基础配送费');
  integer(zone.extraAmount, '续距离费用');
  finite(zone.firstDistance, '基础距离');
  positive(zone.extraDistance, '续距离单位');
  integer(zone.freeThreshold, '包邮门槛');
  integer(zone.minimumAmount, '起送金额');
  boolean(zone.active, '区域启用状态');
  boolean(context.inside, '围栏范围命中');
  boolean(context.excluded, '排除区域命中');
  integer(context.amount, '门槛商品金额');
  finite(context.distance, '直线距离');
  boolean(context.campaign, '平台活动命中');
  if (!zone.active) return denied('UNAVAILABLE', '该配送区域已停用');
  if (context.excluded) return denied('EXCLUDED', '地址位于排除区域');
  if (!context.inside) return denied('OUT_OF_RANGE', '地址超出配送范围');
  if (context.amount < zone.minimumAmount) return denied('BELOW_MINIMUM', `未达到起送金额${money(zone.minimumAmount)}`);
  const cost = zone.mode === 'fixed' ? zone.fixedAmount : steppedCost(zone.firstAmount, zone.firstDistance, zone.extraAmount, zone.extraDistance, context.distance);
  return { ...splitCost(cost, context.amount, zone.freeThreshold, context.campaign), eligible: true, eligibility: 'ELIGIBLE' };
}
