import type { InventoryBalance } from './types'
export interface InventoryUnitTotal { baseUnit: string; onHand: number; reserved: number; available: number }
export function summarizeBalances(rows: Pick<InventoryBalance, 'warehouseId' | 'skuId' | 'baseUnit' | 'onHandBaseUnits' | 'reservedBaseUnits' | 'availableBaseUnits'>[]): InventoryUnitTotal[]
