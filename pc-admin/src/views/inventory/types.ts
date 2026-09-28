export interface Warehouse {
  warehouseId: string
  code: string
  name: string
  isDefault: boolean
  enabled: boolean
}

export interface WarehouseList { items: Warehouse[] }

export interface InventoryBalance {
  warehouseId: string
  warehouseName: string
  skuId: string
  skuCode: string
  productName: string
  baseUnit: string
  onHandBaseUnits: number
  reservedBaseUnits: number
  availableBaseUnits: number
}

export interface Page<T> { items: T[]; page: number; pageSize: number; total: number }

export interface InventorySku {
  skuId: string
  skuCode: string
  productName: string
  baseUnit: string
  saleUnit: string
  ratio: number
  unitVersionId: string
}

export interface InboundSummary {
  inboundId: string
  documentNo: string
  warehouseId: string
  warehouseName: string
  status: 'DRAFT' | 'CONFIRMED'
  revision: number
  reason: string
  createdAt: string
  createdBy: string
  itemCount: number
  totalBaseUnits: number
}

export interface InboundItem {
  skuId: string
  skuCode: string
  productName: string
  quantity: number
  operationUnit: string
  ratio: number
  baseQuantity: number
  baseUnit: string
}

export interface InboundDetail extends InboundSummary { items: InboundItem[] }

export type OutboundReason = 'DAMAGE' | 'SAMPLE' | 'INTERNAL' | 'OTHER'

export interface OutboundSummary {
  outboundId: string
  documentNo: string
  warehouseId: string
  warehouseName: string
  status: 'DRAFT' | 'CONFIRMED'
  revision: number
  reason: OutboundReason
  note: string
  createdAt: string
  createdBy: string
  confirmedAt: string | null
  itemCount: number
  totalBaseUnits: number
}

export interface OutboundDetail extends OutboundSummary { items: InboundItem[] }

export interface InventoryLedger {
  ledgerId: string
  movementType: 'INBOUND' | 'OUTBOUND' | 'ADJUSTMENT' | 'SALE'
  warehouseId: string
  warehouseName: string
  skuId: string
  skuCode: string
  productName: string
  documentNo: string
  documentId: string
  operationUnit: string
  operationQuantity: number
  ratio: number
  deltaBaseUnits: number
  balanceBefore: number
  balanceAfter: number
  reason: string
  note: string
  actorName: string
  occurredAt: string
  baseUnit?: string
  unitVersionId?: string
}

export type StocktakeStatus = 'COUNTING' | 'PENDING_REVIEW' | 'APPROVED'

export interface StocktakeSummary {
  stocktakeId: string
  documentNo: string
  warehouseId: string
  warehouseName: string
  status: StocktakeStatus
  revision: number
  createdAt: string
  createdBy: string
  submittedAt: string | null
  submittedBy: string | null
  reviewedAt: string | null
  reviewedBy: string | null
  reviewNote: string
  itemCount: number
}

export interface StocktakeItem {
  skuId: string
  skuCode: string
  productName: string
  baseUnit: string
  bookAtStartBaseUnits: number
  bookAtSubmitBaseUnits: number | null
  reservedAtSubmitBaseUnits: number | null
  currentBookBaseUnits: number
  currentReservedBaseUnits: number
  bookChanged: boolean
  countedBaseUnits: number | null
  deltaBaseUnits: number | null
  reason: string
}

export interface StocktakeDetail extends StocktakeSummary { items: StocktakeItem[] }
