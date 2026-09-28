export function parseCount(value) {
  const input = String(value).trim()
  if (!/^(0|[1-9]\d*)$/.test(input)) return null
  const count = Number(input)
  return Number.isSafeInteger(count) ? count : null
}

export function buildStocktakeSubmission(items, values) {
  return items.map((item) => {
    const input = values[item.skuId]
    const count = input ? parseCount(input.count) : null
    if (count === null) throw new Error('请填写全部 SKU 的实盘数量，数量须为非负整数。')
    const reason = input.reason.trim()
    if (count !== item.bookAtStartBaseUnits && !reason) {
      throw new Error(`SKU ${item.skuCode || item.skuId} 的实盘与任务账面不同，请填写差异原因。`)
    }
    return { skuId: item.skuId, countedBaseUnits: count, reason }
  })
}
