// A dining visit and its basket only exist in memory; never restore from storage.
let visit = null
let basket = []
function open(value) { if (!value || !value.sessionId || !value.restaurant || !value.table) throw new Error('餐桌访问响应不完整。'); visit = {...value}; basket = []; return current() }
function current() { return visit ? {...visit} : null }
function items() { return basket.map(row => ({...row})) }
function select(row) { if (!visit || typeof row.skuId !== 'string' || !Number.isSafeInteger(row.quantity) || row.quantity < 0) throw new Error('请重新扫码并选择有效数量。'); basket = [...basket.filter(item => item.skuId !== row.skuId), ...(row.quantity ? [{...row}] : [])] }
function clear() { const previous = visit; visit = null; basket = []; return previous }
async function close() { const previous = clear(); if (!previous) return; try { await require('./api').remove(`/api/v1/app/dining-alliance/sessions/${encodeURIComponent(previous.sessionId)}`) } catch (_) { /* Local access is revoked immediately; server expiry remains authoritative. */ } }
function quoteReady(q) { const d=q&&q.dining; if (!d || !d.restaurantId || !d.tableNumber || !d.address || q.addressRequired !== false) return false; const a=[q.goodsTotalFen,q.shippingFeeFen,q.couponDiscountFen,q.pointsDiscountFen,q.payableFen]; return a.every(v=>Number.isSafeInteger(v)&&v>=0)&&q.goodsTotalFen-q.couponDiscountFen-q.pointsDiscountFen+q.shippingFeeFen===q.payableFen&&q.ready===true&&q.orderSubmissionAvailable===true&&!q.confirmRequired }
function amountFen(value) { if (typeof value!=='string'||!/^\d+(\.\d{1,2})?$/.test(value.trim())) throw new Error('请输入最多两位小数的提现金额。'); const [whole,decimal='']=value.trim().split('.'); const result=Number(whole)*100+Number(decimal.padEnd(2,'0')); if (!Number.isSafeInteger(result)||result<1) throw new Error('请输入有效的提现金额。'); return result }
module.exports={open,current,items,select,clear,close,quoteReady,amountFen}
