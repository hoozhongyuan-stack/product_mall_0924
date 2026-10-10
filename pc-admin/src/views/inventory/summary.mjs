// API rows represent warehouse / anchor-SKU physical balances, not sale-SKU quantities.
export function summarizeBalances(rows) {
  const unique = rows.filter((row, index) => rows.findIndex(other =>
    other.warehouseId === row.warehouseId && other.skuId === row.skuId) === index)
  return unique.reduce((groups, row) => {
    const existing = groups.find(group => group.baseUnit === row.baseUnit)
    const total = {
      baseUnit: row.baseUnit,
      onHand: (existing?.onHand ?? 0) + row.onHandBaseUnits,
      reserved: (existing?.reserved ?? 0) + row.reservedBaseUnits,
      available: (existing?.available ?? 0) + row.availableBaseUnits,
    }
    return existing ? groups.map(group => group.baseUnit === row.baseUnit ? total : group) : [...groups, total]
  }, [])
}
