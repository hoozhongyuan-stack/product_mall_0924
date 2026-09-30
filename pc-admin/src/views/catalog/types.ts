export type CategoryStatus = 'ACTIVE' | 'INACTIVE'
export type SaleStatus = 'ON_SALE' | 'OFF_SALE'
export type ProductStatus = 'DRAFT' | 'ON_SALE' | 'OFF_SALE'
import type { Asset } from '../../shared/media'
export type { Asset } from '../../shared/media'

export interface Category {
  id: string
  parentId: string | null
  name: string
  sortOrder: number
  status: CategoryStatus
  revision: number
  productCount?: number
  onSaleProductCount?: number
}

export interface MemberGrade {
  id: string
  code: string
  name: string
  rank: number
  enabled: boolean
}

export interface GradePrice { gradeId: string; priceFen: number }
export interface SkuUnit { baseUnit: string; saleUnit: string; ratio: number }
export interface SkuRow {
  skuId: string
  skuCode: string
  skuRevision: number
  productId: string
  productRevision: number
  productNo: string
  productName: string
  productStatus: ProductStatus
  fulfillmentKind: 'SHIP' | 'REDEEM'
  categoryId: string
  specs: { name: string; value: string }[]
  listPriceFen: number
  gradePrices: GradePrice[]
  saleStatus: SaleStatus
  unit: SkuUnit
}
export interface SkuPage { rows: SkuRow[]; page: number; pageSize: number; total: number }
export interface ProductRow {
  productId: string
  productRevision: number
  productNo: string
  name: string
  categoryId: string
  fulfillmentKind: 'SHIP' | 'REDEEM'
  status: ProductStatus
  mainImage: Asset | null
  minListPriceFen: number | null
  maxListPriceFen: number | null
  skuCount: number
  onSaleSkuCount: number
  matchedSkuIds: string[]
}
export interface ProductPage { rows: ProductRow[]; page: number; pageSize: number; total: number }
export interface ProductSku extends SkuRow { specOptionIds: string[] }
export interface SpecOption { id?: string; clientKey?: string; value: string; sortOrder: number }
export interface SpecAxis { id?: string; clientKey?: string; name: string; sortOrder: number; options: SpecOption[] }
export interface ProductDetail {
  productId: string
  productNo: string
  name: string
  categoryId: string
  fulfillmentKind: 'SHIP' | 'REDEEM'
  redeemValidUntil: string | null
  status: ProductStatus
  descriptionHtml: string
  productRevision: number
  mainImage: Asset | null
  galleryImages: Asset[]
  video: Asset | null
  specAxes: SpecAxis[]
  skus: ProductSku[]
}

export function yuanToFen(value: string): number | null {
  const trimmed = value.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return null
  const [whole, decimal = ''] = trimmed.split('.')
  const fen = Number(whole) * 100 + Number(decimal.padEnd(2, '0'))
  return Number.isSafeInteger(fen) ? fen : null
}

export function fenToYuan(value: number): string {
  return (value / 100).toFixed(2)
}

export async function validateMediaFile(file: File, role: 'main' | 'gallery' | 'video'): Promise<string | null> {
  const isVideo = role === 'video'
  const allowed = isVideo ? ['video/mp4'] : ['image/jpeg', 'image/png']
  const maxBytes = isVideo ? 50 * 1024 * 1024 : 10 * 1024 * 1024
  if (!allowed.includes(file.type)) return isVideo ? '视频仅支持 MP4 文件。' : '图片仅支持 JPG 或 PNG 文件。'
  if (!file.size || file.size > maxBytes) return isVideo ? '视频不得超过 50 MiB。' : '图片不得超过 10 MiB。'
  if (role !== 'main') return null
  try {
    const bitmap = await createImageBitmap(file)
    const square = bitmap.width === bitmap.height
    bitmap.close()
    return square ? null : '主图需要宽高相等的正方形图片。'
  } catch {
    return '图片无法解码，请换一张 JPG 或 PNG 图片。'
  }
}
