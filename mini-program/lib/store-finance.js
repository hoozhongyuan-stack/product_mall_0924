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
function present(account) {
 const balance = account.balance || {}
 return { ...account, pending: amount(balance.pendingFen), available: amount(balance.availableFen), frozen: amount(balance.frozenFen), paid: amount(balance.paidFen),
  income: (account.income || []).map(row => ({ ...row, paidAmount: amount(row.paidFen), costAmount: amount(row.costFen), platformAmount: amount(row.platformFen), freightAmount: amount(row.freightFen), storeAmount: amount(row.storeFen), statusLabel: ({ PENDING: '待结算', SETTLED: '已结算', HELD: '暂缓结算' })[row.status] || row.status })),
  withdrawals: (account.withdrawals || []).map(row => ({ ...row, amount: amount(row.amountFen), statusLabel: WITHDRAWAL_LABELS[row.status] || row.status })),
 }
}
module.exports = { amountFen, withdrawal, present }
