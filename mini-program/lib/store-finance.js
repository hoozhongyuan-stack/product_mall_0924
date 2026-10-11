const WITHDRAWAL_LABELS = {
 PENDING_REVIEW: '待平台审核', APPROVED_PENDING_PAYMENT: '已审核 · 待线下发放', PAID: '已线下发放', REJECTED: '已拒绝 · 金额已解冻',
}
function amountFen(value) {
 const text = String(value || '').trim()
 if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('提现金额须大于零，最多两位小数。')
 const [yuan, decimal = ''] = text.split('.')
 const fen = Number(yuan) * 100 + Number(decimal.padEnd(2, '0'))
 if (!Number.isSafeInteger(fen) || fen <= 0 || fen > 9900000000) throw new Error('提现金额超出有效范围。')
 return fen
}
function withdrawal(fields, availableFen, requestKey) {
 const fen = amountFen(fields.amount)
 if (!Number.isSafeInteger(availableFen) || fen > availableFen) throw new Error('可提现余额不足，请重新加载账户核对。')
 const payeeName = String(fields.payeeName || '').trim()
 const bankName = String(fields.bankName || '').trim()
 const bankAccount = String(fields.bankAccount || '').trim()
 if (!payeeName || payeeName.length > 80) throw new Error('请填写收款人姓名，最多 80 字。')
 if (!bankName || bankName.length > 120) throw new Error('请填写开户行，最多 120 字。')
 if (!/^\d{8,34}$/.test(bankAccount)) throw new Error('请填写 8–34 位数字银行账号。')
 return { requestKey, amountFen: fen, payeeName, bankName, bankAccount }
}
function amount(value) { return Number.isSafeInteger(value) ? `¥${(value / 100).toFixed(2)}` : '暂不可用' }
const INCOME_FILTERS = [{value:'',label:'全部'},{value:'PENDING',label:'待结算'},{value:'SETTLED',label:'已结算'},{value:'HELD',label:'暂缓结算'}]
function maskBank(value) { return value ? `****${String(value).slice(-4)}` : '未提供' }
function dateLabel(value) {
 if (!value) return ''
 const timestamp=Date.parse(value)
 if (!Number.isFinite(timestamp)) return ''
 return new Date(timestamp+8*3600000).toISOString().slice(0,16).replace('T',' ')
}
function difference(left,right) { return Number.isSafeInteger(left)&&Number.isSafeInteger(right)?amount(left-right):'暂不可用' }
function withdrawalView(row) {
 const descriptions={PENDING_REVIEW:'申请金额已冻结，等待平台审核。',APPROVED_PENDING_PAYMENT:'平台已审核，等待线下转账；审核通过不代表到账。',PAID:'平台已记录线下发放，实际到账请核对银行账户。',REJECTED:'申请已驳回，金额已解冻；更正收款信息后可重新申请。'}
 const level=({PENDING_REVIEW:0,APPROVED_PENDING_PAYMENT:1,PAID:2})[row.status]
 return {...row,amount:amount(row.amountFen),bankAccount:maskBank(row.bankAccount),statusLabel:WITHDRAWAL_LABELS[row.status]||'状态待核对',statusTone:row.status==='PAID'?'success':row.status==='REJECTED'?'danger':'pending',statusDescription:descriptions[row.status]||'请重新加载记录核对处理状态。',createdAtLabel:dateLabel(row.createdAt),paidAtLabel:dateLabel(row.paidAt),steps:['提交申请','平台审核','线下发放'].map((label,i)=>({label,complete:level!==undefined&&i<=level,description:i===0?dateLabel(row.createdAt):i===2?dateLabel(row.paidAt):''}))}
}
function present(account) {
 const balance = account.balance || {}
 return { ...account, pending: amount(balance.pendingFen), available: amount(balance.availableFen), frozen: amount(balance.frozenFen), paid: amount(balance.paidFen),
  income: (account.income || []).map(row => ({ ...row, paidAmount: amount(row.paidFen), costAmount: amount(row.costFen), platformAmount: amount(row.platformFen), freightAmount: amount(row.freightFen), beforeDiningAmount:amount(row.beforeDiningFen===undefined?row.storeFen:row.beforeDiningFen), diningCommissionAmount:amount(row.diningCommissionFen===undefined?0:row.diningCommissionFen), storeAmount: amount(row.storeFen), profitAmount:difference(row.paidFen,row.costFen),storeProfitAmount:difference(Number.isSafeInteger(row.costFen)&&Number.isSafeInteger(row.paidFen)?row.paidFen-row.costFen:null,row.platformFen),availableAtLabel:dateLabel(row.availableAt),settledAtLabel:dateLabel(row.settledAt),statusTone:row.status==='SETTLED'?'success':row.status==='HELD'?'danger':'pending',statusLabel: ({ PENDING: '待结算', SETTLED: '已结算', HELD: '暂缓结算' })[row.status] || row.status })),
  withdrawals: (account.withdrawals || []).map(withdrawalView),
 }
}
module.exports = { amountFen, withdrawal, present, maskBank, INCOME_FILTERS }
