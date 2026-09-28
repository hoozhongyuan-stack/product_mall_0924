import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createContext, runInContext } from 'node:vm'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'

const source = readFileSync(fileURLToPath(new URL('../src/views/catalog/spec-editor.ts', import.meta.url)), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const exports = {}
const money = { fenToYuan: (fen) => (fen / 100).toFixed(2), yuanToFen: (value) => {
  if (!/^\d+(\.\d{1,2})?$/.test(value.trim())) return null
  return Math.round(Number(value) * 100)
} }
runInContext(compiled, createContext({ exports, require: () => money }))
const { combinationKey, generateCombinations, initialAxes, initialSkus, toSpecPayload } = exports

const product = {
  productRevision: 3,
  specAxes: [
    { id: 'axis-pack', name: '包装', options: [{ id: 'single', value: '单瓶' }, { id: 'box', value: '整箱' }] },
    { id: 'axis-color', name: '颜色', options: [{ id: 'red', value: '红色' }] },
  ],
  skus: [
    { skuId: 'sku-1', skuRevision: 4, specOptionIds: ['single', 'red'], skuCode: 'SINGLE-RED', listPriceFen: 26800,
      saleStatus: 'OFF_SALE', gradePrices: [{ gradeId: 'silver', priceFen: 24800 }], unit: { baseUnit: '瓶', saleUnit: '瓶', ratio: 1 } },
    { skuId: 'sku-2', skuRevision: 2, specOptionIds: ['box', 'red'], skuCode: 'BOX-RED', listPriceFen: 148000,
      saleStatus: 'ON_SALE', gradePrices: [], unit: { baseUnit: '瓶', saleUnit: '箱', ratio: 6 } },
  ],
}

test('reordering axes retains existing SKU identity and filled values', () => {
  const axes = initialAxes(product).reverse()
  const generated = generateCombinations(axes, initialSkus(product))
  assert.equal(combinationKey(['single', 'red']), combinationKey(['red', 'single']))
  assert.deepEqual(Array.from(generated, (sku) => sku.id), ['sku-1', 'sku-2'])
  assert.equal(generated[0].skuCode, 'SINGLE-RED')
  assert.equal(generated[1].priceYuan, '1480.00')
  assert.equal(generated[1].ratio, 6)
})

test('new and removed combinations are explicit while retained SKU keeps its revision', () => {
  const axes = initialAxes(product)
  axes[0].options = [axes[0].options[0], { clientKey: 'fresh-option', value: '礼盒' }]
  const generated = generateCombinations(axes, initialSkus(product))
  assert.equal(generated[0].id, 'sku-1')
  assert.equal(generated[0].expectedSkuRevision, 4)
  assert.equal(generated[1].id, undefined)
  assert.equal(generated[1].saleStatus, 'OFF_SALE')
  assert.equal(generated.some((sku) => sku.id === 'sku-2'), false)
})

test('payload includes stable IDs and revisions, with valid enabled grade price', () => {
  const payload = toSpecPayload(initialAxes(product), initialSkus(product), [{ id: 'silver', enabled: true }], 3)
  assert.equal(payload.expectedRevision, 3)
  assert.equal(payload.specAxes[0].id, 'axis-pack')
  assert.equal(payload.skus[0].id, 'sku-1')
  assert.equal(payload.skus[0].expectedSkuRevision, 4)
  assert.equal(payload.skus[0].listPriceFen, 26800)
  assert.equal(payload.skus[0].gradePrices[0].priceFen, 24800)
  assert.throws(() => toSpecPayload(initialAxes(product), [{ ...initialSkus(product)[0], skuCode: 'bad code' }], [], 3), /编码/)
})
