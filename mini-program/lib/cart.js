const KEY = 'mall.cart.v1'
const MAX_ITEMS = 50

function read() {
  const value = wx.getStorageSync ? wx.getStorageSync(KEY) : []
  return Array.isArray(value) ? value.filter((row) => row && typeof row.skuId === 'string' &&
    Number.isInteger(row.quantity) && row.quantity > 0).slice(0, MAX_ITEMS) : []
}

function write(rows) {
  const next = rows.slice(0, MAX_ITEMS)
  wx.setStorageSync(KEY, next)
  return next
}

function add(row) {
  if (!row || typeof row.skuId !== 'string' || !Number.isInteger(row.quantity) || row.quantity < 1) {
    throw new Error('请选择有效规格与数量。')
  }
  const rows = read()
  const index = rows.findIndex((item) => item.skuId === row.skuId)
  if (index >= 0) {
    return write(rows.map((item, position) => position === index ?
      { ...item, quantity: Math.min(9999, item.quantity + row.quantity), selected: true } : item))
  }
  if (rows.length >= MAX_ITEMS) throw new Error('购物车最多保留 50 个规格。')
  return write([...rows, { skuId: row.skuId, quantity: row.quantity,
    seenPriceFen: row.seenPriceFen, name: row.name || '', imageUrl: row.imageUrl || '',
    spec: row.spec || '', unit: row.unit || '', selected: true }])
}

function update(skuId, patch) {
  if (patch.quantity !== undefined && (!Number.isInteger(patch.quantity) || patch.quantity < 1 || patch.quantity > 9999)) {
    throw new Error('数量须为 1 至 9999。')
  }
  return write(read().map((row) => row.skuId === skuId ? { ...row, ...patch } : row))
}

function remove(skuId) { return write(read().filter((row) => row.skuId !== skuId)) }
function selected() { return read().filter((row) => row.selected !== false) }
function quoteItems(rows) {
  return rows.map((row) => ({ skuId: row.skuId, quantity: row.quantity,
    ...(Number.isSafeInteger(row.seenPriceFen) ? { seenPriceFen: row.seenPriceFen } : {}) }))
}

module.exports = { read, write, add, update, remove, selected, quoteItems }
