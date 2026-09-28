import assert from 'node:assert/strict'
import test from 'node:test'
import { draftBody, draftIssue, reconcileDraft, parseTemplateRow, parseTemplateSettings } from '../src/views/notifications/draft.mjs'

const rows = [
  { eventType: 'ORDER_PAID', revision: 0, draftAppId: '', draftTemplateId: '', status: 'UNBOUND', enabled: false },
  { eventType: 'ORDER_SHIPPED', revision: 1, draftAppId: 'wx-test', draftTemplateId: '', status: 'INCOMPLETE', enabled: false },
  { eventType: 'REFUND_SUCCEEDED', revision: 2, draftAppId: 'wx-test', draftTemplateId: 'tmpl-test', status: 'DRAFT_UNVERIFIED', enabled: false },
]

test('settings parser requires the three fixed disabled candidates', () => {
  assert.equal(parseTemplateSettings({ events: rows, sendingAvailable: false }).length, 3)
  assert.throws(() => parseTemplateSettings({ events: rows.slice(0, 2), sendingAvailable: false }))
  assert.throws(() => parseTemplateSettings({ events: [rows[0], rows[0], rows[2]], sendingAvailable: false }))
  assert.throws(() => parseTemplateSettings({ events: [{ ...rows[0], enabled: true }, ...rows.slice(1)], sendingAvailable: false }))
  assert.throws(() => parseTemplateSettings({ events: rows, sendingAvailable: true }))
  assert.equal(parseTemplateRow(rows[0]).eventType, 'ORDER_PAID')
  assert.throws(() => parseTemplateRow({ ...rows[0], enabled: true }))
  assert.throws(() => parseTemplateRow({ ...rows[0], revision: -1 }))
})

test('draft validation and request preserve disabled semantics', () => {
  assert.equal(draftIssue({ draftAppId: '', draftTemplateId: '' }), '')
  assert.match(draftIssue({ draftAppId: 'x'.repeat(65), draftTemplateId: '' }), /64/)
  assert.match(draftIssue({ draftAppId: '', draftTemplateId: 'a\nb' }), /字母/)
  assert.match(draftIssue({ draftAppId: 'wx.demo', draftTemplateId: '' }), /字母/)
  assert.throws(() => draftBody({ draftAppId: '', draftTemplateId: '' }, -1))
  assert.deepEqual(draftBody({ draftAppId: ' wx-test ', draftTemplateId: ' tmpl-test ' }, 2), {
    expectedRevision: 2, draftAppId: 'wx-test', draftTemplateId: 'tmpl-test',
  })
})

test('save reconciliation preserves edits made during the request', () => {
  const submitted = { draftAppId: ' wx-test ', draftTemplateId: ' tmpl-test ' }
  const server = { ...rows[2], revision: 3, draftAppId: 'wx-test', draftTemplateId: 'tmpl-test' }
  const settled = reconcileDraft(submitted, submitted, server)
  assert.equal(settled.editedDuringSave, false)
  assert.deepEqual(settled.form, { draftAppId: 'wx-test', draftTemplateId: 'tmpl-test' })
  const newer = { ...submitted, draftTemplateId: 'another-template' }
  const preserved = reconcileDraft(newer, submitted, server)
  assert.equal(preserved.editedDuringSave, true)
  assert.equal(preserved.form.draftTemplateId, 'another-template')
  assert.equal(reconcileDraft({ ...submitted, draftTemplateId: 'bad!value' }, submitted, server).editedDuringSave, true)
})
