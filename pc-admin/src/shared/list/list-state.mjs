const tones = {
  success: ['ACTIVE', 'ENABLED', 'ON_SALE', 'SUCCESS', 'PAID', 'COMPLETED', 'CONFIRMED', 'AVAILABLE', 'SETTLED', 'CONFIGURED'],
  processing: ['APPROVED', 'PROCESSING', 'RUNNING', 'DELIVERING', 'SHIPPED', 'COUNTING', 'WAITING_PAYMENT', 'IN_TRANSIT', 'PARTIAL', 'IN_PROGRESS'],
  pending: ['PENDING', 'PENDING_PAYMENT', 'PENDING_REVIEW', 'PENDING_RECEIPT', 'WAITING_SHIPMENT', 'WAITING_PREPARATION', 'WAITING_DELIVERY', 'WAITING_PICKUP', 'HELD', 'FROZEN', 'QUEUED', 'WAITING_REDEMPTION', 'WAITING_VALIDITY', 'UNCONFIGURED', 'APPROVED_PENDING_PAYMENT'],
  danger: ['REJECTED', 'FAILED', 'DENIED', 'ERROR', 'ANOMALY'],
}
export function statusTone(status) {
  return Object.entries(tones).find(([, values]) => values.includes(String(status)))?.[0] ?? 'neutral'
}
const validSize = size => [20, 50, 100].includes(size) ? size : 20
export function pageCount(total, size) { return Math.max(1, Math.ceil(Math.max(0, Number(total) || 0) / validSize(size))) }
export function pageWindow(rows, page, size) {
  const actualSize = validSize(size), pages = pageCount(rows.length, actualSize)
  const actualPage = Math.min(pages, Math.max(1, Math.trunc(Number(page) || 1)))
  return { items: rows.slice((actualPage - 1) * actualSize, actualPage * actualSize), page: actualPage, pages, total: rows.length }
}
export function selectedOnPage(ids, allowedIds) { return [...new Set(ids)].filter(id => allowedIds.includes(id)) }
export function toggleSelection(ids, id, checked, allowedIds) {
  const current = selectedOnPage(ids, allowedIds)
  return checked && allowedIds.includes(id) ? [...new Set([...current, id])] : current.filter(value => value !== id)
}
// Use only for endpoints returning the complete collection; server pages remain server pages.
export function filterLoadedRows(rows, keyword, values) {
  const query = String(keyword ?? '').trim().toLocaleLowerCase()
  return rows.filter(row => !query || values(row).some(value => String(value ?? '').toLocaleLowerCase().includes(query)))
}
