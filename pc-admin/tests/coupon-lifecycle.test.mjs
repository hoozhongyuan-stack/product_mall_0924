import { test } from 'node:test'
import assert from 'node:assert/strict'
import { campaignLifecycle, issuanceReason } from '../src/views/coupons/lifecycle.mjs'
const now = Date.parse('2026-10-10T00:00:00Z')
const campaign = { status: 'PUBLISHED', validFrom: '2026-10-01T00:00:00Z', validUntil: '2026-10-11T00:00:00Z', issuanceEnabled: true, remainingQuantity: 10, claimMode: 'BOTH' }
test('expiry overrides enabled distribution and rejects issuance at the exact end boundary', () => {
  const expired = { ...campaign, validUntil: '2026-10-10T00:00:00Z' }
  assert.equal(campaignLifecycle(expired, now).label, '已结束')
  assert.equal(issuanceReason(expired, now), '活动已结束，不能继续发券。')
})
test('distinguishes draft, legacy, scheduled, paused, exhausted and current activities', () => {
  for (const [patch, label] of [[{status:'DRAFT'},'草稿'],[{status:'LEGACY'},'历史活动'],[{validFrom:'2026-10-10T01:00:00Z'},'未开始'],[{issuanceEnabled:false},'已暂停'],[{remainingQuantity:0},'已领完'],[{},'进行中']]) {
    assert.equal(campaignLifecycle({...campaign, ...patch}, now).label, label)
  }
  assert.equal(issuanceReason(campaign, now), '')
  assert.equal(issuanceReason({...campaign,claimMode:'SELF'}, now), '活动仅支持用户主动领取，不能后台发券。')
  assert.equal(campaignLifecycle({...campaign,validUntil:'invalid'}, now).label, '有效期异常')
})

test('unknown clock never optimistically enables issuance', () => {
  assert.equal(campaignLifecycle(campaign, Number.NaN).active, false)
  assert.equal(issuanceReason(campaign, Number.NaN), '当前时间异常，请刷新后重试。')
})
