import { describe, expect, it } from 'vitest'
import { ApiError } from '../../src/api'
import { checkMessages, credentialIssue, readIntegration, safeFailure, type Integration } from '../../src/views/integrations/wechat'
const value: Integration = { revision: 2, source: 'MANAGED', appId: 'wx0123456789abcdef', secretConfigured: true, keyAvailable: true, identityBinding: { status: 'EMPTY', appId: null }, paymentAppIdStatus: 'NOT_CONFIGURED', notificationsStatus: 'NOT_VERIFIED', lastCheck: null }

describe('public integration DTO and credential validation', () => {
  it('whitelists public metadata and discards a check from another revision', () => {
    expect(readIntegration({ ...value, appSecret: 'do-not-render', accessToken: 'do-not-render' })).toEqual(value)
    expect(readIntegration({ ...value, lastCheck: { revision: 1, status: 'SUCCESS', code: 'OK', checkedAt: '2026-09-28T01:00:00Z' } }).lastCheck).toBeNull()
  })
  it.each([null, [], { ...value, revision: -1 }, { ...value, source: 'UNKNOWN' }, { ...value, secretConfigured: 'true' }, { ...value, keyAvailable: undefined }, { ...value, appId: 12 }, { ...value, identityBinding: {} }, { ...value, identityBinding: { status: 'BOUND', appId: null } }, { ...value, paymentAppIdStatus: 'UNKNOWN' }, { ...value, notificationsStatus: 'SUCCESS' }, { ...value, lastCheck: {} }, { ...value, lastCheck: { revision: 2, status: 'SUCCESS', code: 'OK', checkedAt: 'bad-date' } }])('rejects malformed metadata', input => {
    expect(() => readIntegration(input)).toThrow()
  })
  it('accepts uppercase IDs and opaque secrets without changing their content', () => {
    expect(credentialIssue(value, 'wxABCDEF0123456789', 'REPLACE', '  密钥 with spaces  ')).toBe('')
    expect(credentialIssue(value, value.appId, 'REPLACE', '🔒'.repeat(256))).toBe('')
    expect(credentialIssue(value, value.appId, 'KEEP', '')).toBe('')
    expect(credentialIssue(value, 'invalid', 'REPLACE', 'opaque')).toContain('AppID')
  })
  it.each(['', '  ', 'x\u0000y', 'x\u0085y', 'x'.repeat(257)])('rejects an invalid new secret', secret => {
    expect(credentialIssue(value, value.appId, 'REPLACE', secret)).toContain('AppSecret')
  })
  it('requires a replacement for first setup or changing AppID, and obeys identity anchors', () => {
    expect(credentialIssue({ ...value, secretConfigured: false }, value.appId, 'KEEP', '')).toContain('首次配置')
    expect(credentialIssue(value, 'wx1111111111111111', 'KEEP', '')).toContain('修改 AppID')
    const bound: Integration = { ...value, identityBinding: { status: 'BOUND', appId: 'wx1111111111111111' } }
    expect(credentialIssue(bound, value.appId, 'REPLACE', 'opaque')).toContain('会员身份绑定')
    expect(credentialIssue(bound, 'wx1111111111111111', 'REPLACE', 'opaque')).toBe('')
    const multiple: Integration = { ...value, identityBinding: { status: 'MULTIPLE', appId: null } }
    expect(credentialIssue(multiple, 'wx1111111111111111', 'REPLACE', 'opaque')).toContain('多个 AppID')
    expect(credentialIssue(multiple, value.appId, 'REPLACE', 'opaque')).toBe('')
  })
  it('uses only safe local errors and distinct platform result labels', () => {
    for (const code of ['REVISION_CONFLICT', 'APP_ID_LOCKED', 'CREDENTIALS_NOT_CONFIGURED', 'CREDENTIALS_UNAVAILABLE', 'VALIDATION_FAILED', 'RATE_LIMITED', 'UNKNOWN']) expect(safeFailure(new ApiError('private diagnostic', 400, code))).not.toContain('private diagnostic')
    expect(safeFailure(new ApiError('private diagnostic', 401, 'UNKNOWN'))).toContain('登录已失效')
    expect(safeFailure(new ApiError('private diagnostic', 403, 'UNKNOWN'))).toContain('权限或密码')
    expect(safeFailure(new Error('private diagnostic'))).toContain('无法确认')
    expect(checkMessages.ADMIN_REJECTED).toContain('管理员已拒绝')
  })
})
