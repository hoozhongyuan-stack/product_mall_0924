import type { MemberGrade, ProductDetail, SpecAxis } from './types'
import { fenToYuan, yuanToFen } from './types'

export interface EditableOption { id?: string; clientKey: string; value: string }
export interface EditableAxis { id?: string; clientKey: string; name: string; options: EditableOption[] }
export interface EditableSku {
  id?: string
  expectedSkuRevision?: number
  optionKeys: string[]
  label: string
  skuCode: string
  priceYuan: string
  saleStatus: 'ON_SALE' | 'OFF_SALE'
  gradePrices: Record<string, string>
  baseUnit: string
  saleUnit: string
  ratio: number
}

export function combinationKey(keys: string[]): string { return [...keys].sort().join('|') || 'single' }

export function initialAxes(detail: ProductDetail): EditableAxis[] {
  return detail.specAxes.map((axis) => ({ id: axis.id, clientKey: axis.id!, name: axis.name,
    options: axis.options.map((option) => ({ id: option.id, clientKey: option.id!, value: option.value })) }))
}

export function initialSkus(detail: ProductDetail): EditableSku[] {
  const optionValues = new Map(detail.specAxes.flatMap((axis) => axis.options.map((option) => [option.id, option.value] as const)))
  return detail.skus.map((sku) => ({ id: sku.skuId, expectedSkuRevision: sku.skuRevision,
    optionKeys: sku.specOptionIds, label: sku.specOptionIds.map((id) => optionValues.get(id) || id).join(' / '),
    skuCode: sku.skuCode, priceYuan: fenToYuan(sku.listPriceFen), saleStatus: sku.saleStatus,
    gradePrices: Object.fromEntries(sku.gradePrices.map((price) => [price.gradeId, fenToYuan(price.priceFen)])),
    baseUnit: sku.unit.baseUnit, saleUnit: sku.unit.saleUnit, ratio: sku.unit.ratio }))
}

export function validateAxes(axes: EditableAxis[]): string | null {
  if (axes.length > 2) return '最多可设置 2 个规格项。'
  const names = axes.map((axis) => axis.name.trim())
  if (names.some((name) => !name || name.length > 60) || new Set(names).size !== names.length) return '规格项名称不能为空或重复，且最长 60 个字符。'
  for (const axis of axes) {
    const values = axis.options.map((option) => option.value.trim())
    if (!values.length || values.length > 20 || values.some((value) => !value || value.length > 60) || new Set(values).size !== values.length) {
      return '每个规格项须有 1—20 个不重复的规格值，且值不能为空或超过 60 个字符。'
    }
  }
  if (axes.reduce((count, axis) => count * axis.options.length, 1) > 100) return 'SKU 组合最多 100 个，请减少规格值。'
  return null
}

export function generateCombinations(axes: EditableAxis[], previous: EditableSku[]): EditableSku[] {
  const old = new Map(previous.map((sku) => [combinationKey(sku.optionKeys), sku]))
  const combinations = axes.reduce<{ optionKeys: string[]; label: string }[]>((items, axis) =>
    items.flatMap((item) => axis.options.map((option) => ({ optionKeys: [...item.optionKeys, option.clientKey],
      label: [...(item.label ? [item.label] : []), option.value.trim()].join(' / ') }))),
  [{ optionKeys: [], label: '' }])
  return combinations.map((item) => ({ id: undefined, expectedSkuRevision: undefined, skuCode: '', priceYuan: '',
    saleStatus: 'OFF_SALE', gradePrices: {}, baseUnit: '件', saleUnit: '件', ratio: 1,
    ...old.get(combinationKey(item.optionKeys)), ...item }))
}

export function toSpecPayload(axes: EditableAxis[], skus: EditableSku[], grades: MemberGrade[], expectedRevision: number) {
  const axisError = validateAxes(axes)
  if (axisError) throw new Error(axisError)
  const enabledGrades = new Set(grades.filter((grade) => grade.enabled).map((grade) => grade.id))
  const codes = skus.map((sku) => sku.skuCode.trim())
  if (codes.some((code) => !/^[A-Za-z0-9_-]{1,64}$/.test(code)) || new Set(codes.map((code) => code.toUpperCase())).size !== codes.length) {
    throw new Error('请为每个 SKU 填写不重复的编码，只能使用字母、数字、短横线和下划线。')
  }
  const prices = skus.map((sku) => yuanToFen(sku.priceYuan))
  if (prices.some((value) => value === null)) throw new Error('日常价须为非负金额，最多两位小数。')
  for (const sku of skus) {
    if (!sku.baseUnit.trim() || !sku.saleUnit.trim() || !Number.isInteger(sku.ratio) || sku.ratio < 1) {
      throw new Error(`SKU ${sku.skuCode || sku.label} 的单位和换算比无效。`)
    }
    for (const [gradeId, input] of Object.entries(sku.gradePrices)) {
      if (input.trim() && enabledGrades.has(gradeId) && yuanToFen(input) === null) throw new Error(`SKU ${sku.skuCode} 的等级价格式无效。`)
    }
  }
  return { expectedRevision,
    specAxes: axes.map((axis, axisIndex) => ({ ...(axis.id ? { id: axis.id } : {}), clientKey: axis.clientKey,
      name: axis.name.trim(), sortOrder: axisIndex, options: axis.options.map((option, optionIndex) =>
        ({ ...(option.id ? { id: option.id } : {}), clientKey: option.clientKey,
          value: option.value.trim(), sortOrder: optionIndex })) })),
    skus: skus.map((sku, index) => ({ ...(sku.id ? { id: sku.id, expectedSkuRevision: sku.expectedSkuRevision } : {}),
      skuCode: sku.skuCode.trim(), specOptionKeys: sku.optionKeys, listPriceFen: prices[index]!,
      saleStatus: sku.saleStatus,
      gradePrices: Object.entries(sku.gradePrices).filter(([id, input]) => enabledGrades.has(id) && input.trim())
        .map(([gradeId, input]) => ({ gradeId, priceFen: yuanToFen(input)! })),
      unit: { baseUnit: sku.baseUnit.trim(), saleUnit: sku.saleUnit.trim(), ratio: sku.ratio } })) }
}

export function axesMatchDetail(axes: EditableAxis[], original: SpecAxis[]): boolean {
  return JSON.stringify(axes.map((axis) => [axis.id, axis.name, axis.options.map((option) => [option.id, option.value])])) ===
    JSON.stringify(original.map((axis) => [axis.id, axis.name, axis.options.map((option) => [option.id, option.value])]))
}
