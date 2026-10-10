export function campaignLifecycle(campaign, now = Date.now()) {
  if (campaign.status === 'DRAFT') return { label: '草稿', active: false, reason: '活动尚未发布，不能发券。' }
  if (campaign.status === 'LEGACY') return { label: '历史活动', active: false, reason: '历史活动仅供查看，不能新增发券。' }
  const start = Date.parse(campaign.validFrom), end = Date.parse(campaign.validUntil)
  if (!Number.isFinite(now)) return { label: '时间异常', active: false, reason: '当前时间异常，请刷新后重试。' }
  if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) return { label: '有效期异常', active: false, reason: '活动有效期异常，请重新读取活动。' }
  if (now >= end) return { label: '已结束', active: false, reason: '活动已结束，不能继续发券。' }
  if (now < start) return { label: '未开始', active: false, reason: '活动尚未开始，暂不能发券。' }
  if (!campaign.issuanceEnabled) return { label: '已暂停', active: false, reason: '活动已暂停新增领取与发放，不能继续发券。' }
  if (campaign.remainingQuantity <= 0) return { label: '已领完', active: false, reason: '活动剩余数量为 0，不能继续发券。' }
  return { label: '进行中', active: true, reason: '' }
}
export function issuanceReason(campaign, now = Date.now()) {
  const lifecycle = campaignLifecycle(campaign, now)
  if (lifecycle.reason) return lifecycle.reason
  return campaign.claimMode === 'SELF' ? '活动仅支持用户主动领取，不能后台发券。' : ''
}
