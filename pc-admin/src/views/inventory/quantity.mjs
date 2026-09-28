export function parsePositiveQuantity(value) {
  const text = String(value).trim()
  if (!/^\d+$/.test(text)) return null
  const quantity = Number(text)
  return Number.isSafeInteger(quantity) && quantity > 0 ? quantity : null
}
