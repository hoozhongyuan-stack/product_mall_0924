import type { MemberGrade, ProductDetail, SpecAxis, UnitConversion } from './types'
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

// A fresh generated row has only default units. Protect every user-entered field
// before removing a new combination, including unit-only and conversion-only drafts.
export function hasDraftSkuInput(sku: EditableSku): boolean {
  return Boolean(sku.skuCode || sku.priceYuan || Object.values(sku.gradePrices).some(value => value.trim())
    || sku.baseUnit !== '件' || sku.saleUnit !== '件' || sku.ratio !== 1)
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

export function generateCombinations(axes: EditableAxis[], previous: EditableSku[], conversion: UnitConversion | null = null): EditableSku[] {
  const old = new Map(previous.map((sku) => [combinationKey(sku.optionKeys), sku]))
  const combinations = axes.reduce<{ optionKeys: string[]; label: string }[]>((items, axis) =>
    items.flatMap((item) => axis.options.map((option) => ({ optionKeys: [...item.optionKeys, option.clientKey],
      label: [...(item.label ? [item.label] : []), option.value.trim()].join(' / ') }))),
  [{ optionKeys: [], label: '' }])
  return deriveSkuUnits(axes, combinations.map((item) => ({ id: undefined, expectedSkuRevision: undefined, skuCode: '', priceYuan: '',
    saleStatus: 'OFF_SALE', gradePrices: {}, baseUnit: '件', saleUnit: '件', ratio: 1,
    ...old.get(combinationKey(item.optionKeys)), ...item })), conversion)
}

export function toSpecPayload(axes: EditableAxis[], skus: EditableSku[], grades: MemberGrade[], expectedRevision: number, unitConversion: UnitConversion | null = null) {
  const conversionError = validateUnitConversion(axes, unitConversion)
  if (conversionError) throw new Error(conversionError)
  skus = deriveSkuUnits(axes, skus, unitConversion)
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
  return { expectedRevision, unitConversion,
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

export function validateUnitConversion(axes: EditableAxis[], conversion: UnitConversion | null): string | null {
  if (!conversion) return null
  const axis = axes.find(item => item.clientKey === conversion.axisKey)
  if (!axis || !axis.options.some(item => item.clientKey === conversion.baseOptionKey)) return '请选择有效的基本单位。'
  if (axis.options.some(option => !option.value.trim() || option.value.trim().length > 30)) return '单位名称不能为空，且最长 30 个字符。'
  const ratios = new Map(conversion.ratios.map(item => [item.optionKey, item.ratio]))
  if (conversion.ratios.length !== axis.options.length || ratios.size !== axis.options.length
    || ratios.get(conversion.baseOptionKey) !== 1 || axis.options.some(option => !Number.isSafeInteger(ratios.get(option.clientKey)) || ratios.get(option.clientKey)! < 1 || ratios.get(option.clientKey)! > 1_000_000_000)) return '每个单位须配置正整数换算比，基本单位的换算比为 1。'
  return null
}

export function deriveSkuUnits(axes: EditableAxis[], skus: EditableSku[], conversion: UnitConversion | null): EditableSku[] {
  if (!conversion) return skus
  const axis = axes.find(item => item.clientKey === conversion.axisKey)
  const base = axis?.options.find(item => item.clientKey === conversion.baseOptionKey)
  if (!axis || !base) return skus
  return skus.map(sku => {
    const option = axis.options.find(item => sku.optionKeys.includes(item.clientKey))
    const ratio = conversion.ratios.find(item => item.optionKey === option?.clientKey)?.ratio
    return option && ratio !== undefined ? { ...sku, baseUnit: base.value.trim(), saleUnit: option.value.trim(), ratio } : sku
  })
}
