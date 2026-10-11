// A dining visit and its basket only exist in memory; never restore from storage.
let visit = null
let basket = []
let scope = null
function identity() { return typeof wx !== 'undefined' && wx.getStorageSync ? wx.getStorageSync('mall.memberToken') || '' : '' }
function open(value) { if (!value || !value.sessionId || !value.restaurant || !value.table) throw new Error('餐桌访问响应不完整。'); visit = copyVisit(value); scope = { sessionId: value.sessionId, identity: identity() }; basket = []; return current() }
function copyVisit(value) { return JSON.parse(JSON.stringify(value)) }
function replaceItems(rows) {
 if (!current() || !Array.isArray(rows) || rows.some(row => !row || typeof row.skuId !== 'string' || !Number.isSafeInteger(row.quantity) || row.quantity < 1)) throw new Error('请重新扫码并选择有效数量。')
 basket = rows.map(row => ({...row}))
}
function current() { if (visit && scope.identity !== identity()) clear(); return visit ? copyVisit(visit) : null }
function query() { current(); return scope ? { diningSessionId: scope.sessionId } : null }
function scoped() { return !!scope }
function leave() { close(); scope = null }
function isInternalRoute(url) { return /^\/?pages\/(home\/home|index\/index|product\/detail|micro\/detail|cart\/cart|checkout\/checkout|dining\/(shop|checkout)|member\/index|orders\/(list|detail))(?:[?]|$)/.test(url) }
function navigate(url, mode = 'navigateTo', options = {}) {
 if (scope) {
  if (isInternalRoute(url)) {
   if (!current()) { if (wx.showToast) wx.showToast({title:'餐桌访问已结束，请重新扫码。',icon:'none'}); return false }
   if (url === '/pages/home/home') url = '/pages/dining/shop?visit=1'
   if (url === '/pages/checkout/checkout') url = '/pages/dining/checkout'
  } else leave()
 }
 wx[mode]({...options,url}); return true
}
function items() { if (!current()) return []; return basket.map(row => ({...row})) }
function select(row) { if (!current() || typeof row.skuId !== 'string' || !Number.isSafeInteger(row.quantity) || row.quantity < 0) throw new Error('请重新扫码并选择有效数量。'); basket = [...basket.filter(item => item.skuId !== row.skuId), ...(row.quantity ? [{...row}] : [])] }
function clear() { const previous = visit; visit = null; basket = []; return previous }
async function close() { const previous = clear(); if (!previous) return; try { await require('./api').remove(`/api/v1/app/dining-alliance/sessions/${encodeURIComponent(previous.sessionId)}`) } catch (_) { /* Local access is revoked immediately; server expiry remains authoritative. */ } }
function quoteReady(q) { const d=q&&q.dining; if (!d || !d.restaurantId || !d.tableNumber || !d.address || q.addressRequired !== false) return false; const a=[q.goodsTotalFen,q.shippingFeeFen,q.couponDiscountFen,q.pointsDiscountFen,q.payableFen]; return a.every(v=>Number.isSafeInteger(v)&&v>=0)&&q.goodsTotalFen-q.couponDiscountFen-q.pointsDiscountFen+q.shippingFeeFen===q.payableFen&&q.ready===true&&q.orderSubmissionAvailable===true&&!q.confirmRequired }
function amountFen(value) { if (typeof value!=='string'||!/^\d+(\.\d{1,2})?$/.test(value.trim())) throw new Error('请输入最多两位小数的提现金额。'); const [whole,decimal='']=value.trim().split('.'); const result=Number(whole)*100+Number(decimal.padEnd(2,'0')); if (!Number.isSafeInteger(result)||result<1) throw new Error('请输入有效的提现金额。'); return result }
module.exports={open,current,items,select,replaceItems,clear,close,leave,query,scoped,navigate,isInternalRoute,quoteReady,amountFen}
