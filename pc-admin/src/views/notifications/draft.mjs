export const EVENT_TYPES = Object.freeze(['ORDER_PAID', 'ORDER_SHIPPED', 'REFUND_SUCCEEDED'])
const STATUSES = new Set(['UNBOUND', 'INCOMPLETE', 'DRAFT_UNVERIFIED'])

export function parseTemplateRow(row) {
  if (!row || !EVENT_TYPES.includes(row.eventType) ||
      !Number.isInteger(row.revision) || row.revision < 0 ||
      typeof row.draftAppId !== 'string' || typeof row.draftTemplateId !== 'string' ||
      !STATUSES.has(row.status) || row.enabled !== false) {
    throw new Error('订阅消息配置响应不可信，请重新读取。')
  }
  return { ...row }
}

export function parseTemplateSettings(value) {
  if (!value || value.sendingAvailable !== false || !Array.isArray(value.events) ||
      value.events.length !== EVENT_TYPES.length) {
    throw new Error('订阅消息配置响应不完整，请重新读取。')
  }
  const byType = new Map()
  for (const row of value.events) {
    const parsed = parseTemplateRow(row)
    if (byType.has(parsed.eventType)) throw new Error('订阅消息配置存在重复候选事件。')
    byType.set(parsed.eventType, parsed)
  }
  if (byType.size !== EVENT_TYPES.length) throw new Error('订阅消息配置缺少候选事件。')
  return EVENT_TYPES.map(eventType => byType.get(eventType))
}

export function draftIssue(form) {
  if (!form || typeof form.draftAppId !== 'string' || typeof form.draftTemplateId !== 'string') {
    return '配置字段格式不正确。'
  }
  if (form.draftAppId.trim().length > 64) return '小程序 AppID 不能超过 64 字符。'
  if (form.draftTemplateId.trim().length > 128) return '模板 ID 不能超过 128 字符。'
  if (!/^[A-Za-z0-9_-]*$/.test(form.draftAppId.trim()) ||
      !/^[A-Za-z0-9_-]*$/.test(form.draftTemplateId.trim())) {
    return '配置仅能包含英文字母、数字、下划线或短横线。'
  }
  return ''
}

export function draftBody(form, expectedRevision) {
  const issue = draftIssue(form)
  if (issue) throw new Error(issue)
  if (!Number.isInteger(expectedRevision) || expectedRevision < 0) {
    throw new Error('配置修订号不正确，请重新读取。')
  }
  return { expectedRevision, draftAppId: form.draftAppId.trim(), draftTemplateId: form.draftTemplateId.trim() }
}

export function reconcileDraft(current, submitted, server) {
  const normalizedSubmitted = draftBody(submitted, 0)
  const editedDuringSave = current.draftAppId.trim() !== normalizedSubmitted.draftAppId ||
    current.draftTemplateId.trim() !== normalizedSubmitted.draftTemplateId
  return {
    editedDuringSave,
    form: editedDuringSave
      ? { draftAppId: current.draftAppId, draftTemplateId: current.draftTemplateId }
      : { draftAppId: server.draftAppId, draftTemplateId: server.draftTemplateId },
  }
}
