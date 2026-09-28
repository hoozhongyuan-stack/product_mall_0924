const { money } = require('./catalog')
const { dateLabel } = require('./orders')
const POINT_LABELS = { GRANT: '积分发放', EARN: '消费奖励', RETURN: '退款返还抵扣积分', CLAWBACK: '退款扣回奖励积分', EXPIRE: '积分到期扣账', RESERVE: '订单冻结积分', RELEASE: '关单释放积分', CONSUME: '付款核销积分' }
const CAUSE_LABELS = { ORDER_COMPLETED: '订单履约完成', REFUND_COMPLETED: '退款完成', FULFILLMENT_REVERSED: '核销撤销', POINTS_EXPIRED: '积分到期扣账' }
function integer(value, minimum = 0) { return Number.isSafeInteger(value) && value >= minimum }
function presentOverview(value) {
  if (!value || typeof value.id !== 'string' || !value.grade || typeof value.grade.name !== 'string' ||
      !integer(value.effectiveSpendFen) || !value.points || !integer(value.points.settledPoints, -Number.MAX_SAFE_INTEGER) ||
      !['frozenPoints', 'availablePoints', 'debtPoints', 'expiredPendingPoints', 'expiringPoints'].every((key) => integer(value.points[key])) ||
      !value.rules || !value.rules.points || !Array.isArray(value.rules.grades)) throw new Error('会员资料不完整，请重新加载。')
  const p = value.rules.points
  if (!['earnUnitFen', 'earnPoints', 'deductPoints', 'deductFen', 'maxPercent', 'validDays', 'refundValidDays'].every((key) => integer(p[key]))) throw new Error('会员规则不完整，请重新加载。')
  if (!value.rules.grades.every((grade) => grade && typeof grade.name === 'string' && integer(grade.minimumSpendFen))) throw new Error('会员等级规则不完整，请重新加载。')
  return { ...value, gradeRuleCopy: value.gradePolicyRevision > 0 ? `会员已启用等级规则版本 ${value.gradePolicyRevision}` : '尚未通过完成订单启用等级规则。', spendLabel: money(value.effectiveSpendFen), gradeDateLabel: dateLabel(value.gradeEffectiveAt),
    points: { ...value.points, nextExpiryLabel: dateLabel(value.points.nextExpiryAt) },
    grades: value.rules.grades.map((grade) => ({ ...grade, minimumLabel: money(grade.minimumSpendFen) })),
    rulesCopy: `有效消费每 ${money(p.earnUnitFen)} 获得 ${p.earnPoints} 积分；${p.deductPoints} 积分可抵 ${money(p.deductFen)}，抵扣上限 ${p.maxPercent}%。`,
    expiryCopy: `积分有效期 ${p.validDays} 天。退款返还的已过期抵扣积分给予 ${p.refundValidDays} 天有效期。`,
    snapshotCopy: '此处为当前规则，历史成交和积分快照保留。新规则订单履约完成且无进行中售后时，会员启用较新等级规则；既有订单调整消费时不退回旧门槛。'  }
}
function presentPoint(row) {
  if (!row || typeof row.id !== 'string' || typeof row.kind !== 'string' || !integer(row.amount, -Number.MAX_SAFE_INTEGER) || !['BALANCE', 'FREEZE', 'RELEASE'].includes(row.effect) || (row.balance !== null && !integer(row.balance, -Number.MAX_SAFE_INTEGER))) throw new Error('积分明细不完整，请重新加载。')
  return { ...row, reasonLabel: CAUSE_LABELS[row.sourceRef] || POINT_LABELS[row.kind] || '积分变动', kindLabel: POINT_LABELS[row.kind] || '积分变动', dateLabel: dateLabel(row.createdAt),
    amountLabel: row.effect === 'BALANCE' ? `${row.amount > 0 ? '+' : ''}${row.amount}` : `${Math.abs(row.amount)} 积分`,
    effectLabel: row.effect === 'FREEZE' ? '冻结变动，不改变积分账面余额' : row.effect === 'RELEASE' ? '释放冻结，不重复发放积分' : '积分余额变动',
    balanceLabel: row.effect === 'BALANCE' && row.balance !== null ? String(row.balance) : '', expiryLabel: dateLabel(row.expiresAt) }
}
function presentList(value, page, presenter = presentPoint) {
  if (!value || !Array.isArray(value.items) || !value.pagination || value.pagination.page !== page || !integer(value.pagination.total) || value.pagination.pageSize !== 20 || value.items.length > 20) throw new Error('明细列表不完整，请重新加载。')
  return { rows: value.items.map(presenter), page, total: value.pagination.total }
}
function presentConsumption(row) {
  if (!row || typeof row.id !== 'string' || !integer(row.amountFen, -Number.MAX_SAFE_INTEGER) || !integer(row.balanceFen) || !row.gradeBefore || !row.gradeAfter || typeof row.gradeBefore.name !== 'string' || typeof row.gradeAfter.name !== 'string') throw new Error('消费明细不完整，请重新加载。')
  return { ...row, amountLabel: `${row.amountFen < 0 ? '-' : row.amountFen > 0 ? '+' : ''}${money(Math.abs(row.amountFen))}`,
    balanceLabel: money(row.balanceFen), dateLabel: dateLabel(row.createdAt),
    reasonLabel: CAUSE_LABELS[row.sourceRef] || '订单结算调整', gradeLabel: row.gradeBefore.name === row.gradeAfter.name ? `等级保持 ${row.gradeAfter.name}` : `${row.gradeBefore.name} → ${row.gradeAfter.name}` }
}
module.exports = { presentOverview, presentPoint, presentList, presentConsumption }
