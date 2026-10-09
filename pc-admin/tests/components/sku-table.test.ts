import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SkuTable from '../../src/views/catalog/SkuTable.vue'
import { confirmAction } from '../../src/shared/confirm'
import type { EditableSku } from '../../src/views/catalog/spec-editor'
vi.mock('../../src/shared/confirm', () => ({ confirmAction: vi.fn() }))
const row = (n: number): EditableSku => ({ optionKeys: [`option-${n}`], label: `规格 ${n}`, skuCode: `S${n}`, priceYuan: '10.00', saleStatus: 'OFF_SALE', gradePrices: { gold: '9.00' }, baseUnit: '瓶', saleUnit: '箱', ratio: 6 })
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.clearAllMocks() })
function setup(rows = [row(0), row(1)], disabled = false) {
  const wrapper = mount(SkuTable, { props: { skus: rows, axes: [{ clientKey: 'axis', name: '包装', options: rows.map((_, n) => ({ clientKey: `option-${n}`, value: `规格 ${n}` })) }], grades: [{ id: 'gold', code: 'GOLD', name: '金卡', rank: 1, enabled: true }], disabled, showErrors: false } })
  wrappers.push(wrapper)
  return wrapper
}
async function batch(wrapper: ReturnType<typeof mount>, value: string) {
  await wrapper.get('[aria-label="批量价格（元）"]').setValue(value)
  await wrapper.get('[data-action="batch-price"]').trigger('click')
  await flushPromises()
}
describe('SKU table editing', () => {
  it('renders independent specification columns, grade prices and conversion hints for 100 combinations', () => {
    const wrapper = setup(Array.from({ length: 100 }, (_, n) => row(n)))
    expect(wrapper.findAll('tbody tr')).toHaveLength(100)
    expect(wrapper.findAll('thead th').map(h => h.text())).toContain('包装')
    expect(wrapper.text()).toContain('金卡价（元）')
    expect(wrapper.text()).toContain('1 箱＝6 瓶')
  })
  it('changes only selected rows after explicit overwrite confirmation', async () => {
    vi.mocked(confirmAction).mockResolvedValue(true)
    const wrapper = setup()
    await wrapper.get('[aria-label="选择 SKU S1"]').setValue(true)
    await batch(wrapper, '12.50')
    const rows = wrapper.emitted('update:skus')!.at(-1)![0] as EditableSku[]
    expect(rows[0]!.priceYuan).toBe('10.00')
    expect(rows[1]!.priceYuan).toBe('12.50')
    expect(confirmAction).toHaveBeenCalledWith(expect.stringContaining('1'))
  })
  it('cancels overwriting and rejects invalid amounts without changing rows', async () => {
    vi.mocked(confirmAction).mockResolvedValue(false)
    const wrapper = setup()
    await wrapper.get('[aria-label="选择 SKU S0"]').setValue(true)
    await batch(wrapper, '7.00')
    expect(wrapper.emitted('update:skus')).toBeUndefined()
    await batch(wrapper, '7.001')
    expect(wrapper.text()).toContain('最多两位小数')
    expect(confirmAction).toHaveBeenCalledTimes(1)
  })
  it('supports a selected member grade and leaves list prices unchanged', async () => {
    vi.mocked(confirmAction).mockResolvedValue(true)
    const wrapper = setup()
    await wrapper.get('[aria-label="选择全部 SKU"]').setValue(true)
    await wrapper.get('[aria-label="批量价格字段"]').setValue('gold')
    await batch(wrapper, '8.50')
    const rows = wrapper.emitted('update:skus')!.at(-1)![0] as EditableSku[]
    expect(rows.every(r => r.gradePrices.gold === '8.50' && r.priceYuan === '10.00')).toBe(true)
  })
  it('rechecks disabled state after awaiting confirmation', async () => {
    let resolve!: (accepted: boolean) => void
    vi.mocked(confirmAction).mockImplementation(() => new Promise(done => { resolve = done }))
    const wrapper = setup()
    await wrapper.get('[aria-label="选择全部 SKU"]').setValue(true)
    await batch(wrapper, '8.00')
    await wrapper.setProps({ disabled: true })
    resolve(true)
    await flushPromises()
    expect(wrapper.emitted('update:skus')).toBeUndefined()
  })
  it('marks duplicate codes and invalid cell values after validation', async () => {
    const wrapper = setup([{ ...row(0), skuCode: 'same', ratio: 1, baseUnit: '', saleUnit: '' }, { ...row(1), skuCode: 'SAME', priceYuan: 'bad' }])
    await wrapper.setProps({ showErrors: true })
    expect(wrapper.findAll('[aria-invalid="true"]').length).toBeGreaterThanOrEqual(4)
  })
  it('emits immutable edits for all editable fields including a member price', async () => {
    const wrapper = setup([{ ...row(0), baseUnit: '瓶', saleUnit: '瓶', ratio: 1 }, row(1)])
    const original = wrapper.props('skus') as EditableSku[]
    await wrapper.get('[aria-label="SKU 编码 规格 0"]').setValue('NEW-CODE')
    expect((wrapper.emitted('update:skus')!.at(-1)![0] as EditableSku[])[0]!.skuCode).toBe('NEW-CODE')
    await wrapper.get('[aria-label="日常价 S0"]').setValue('19.00')
    await wrapper.get('[aria-label="金卡价 S0"]').setValue('18.00')
    expect((wrapper.emitted('update:skus')!.at(-1)![0] as EditableSku[])[0]!.gradePrices.gold).toBe('18.00')
    await wrapper.get('[aria-label="单位 S0"]').setValue('支')
    expect((wrapper.emitted('update:skus')!.at(-1)![0] as EditableSku[])[0]!).toMatchObject({ baseUnit: '支', saleUnit: '支', ratio: 1 })
    expect(original[0]!.skuCode).toBe('S0')
    expect(original[0]!.gradePrices.gold).toBe('9.00')
  })
  it('removes selection when a combination disappears and supports unchecking rows', async () => {
    const wrapper = setup()
    await wrapper.get('[aria-label="选择全部 SKU"]').setValue(true)
    await wrapper.get('[aria-label="选择 SKU S0"]').setValue(false)
    expect(wrapper.text()).toContain('已选 1 / 2')
    await wrapper.setProps({ skus: [row(0)] })
    expect(wrapper.text()).toContain('已选 0 / 1')
    await wrapper.get('[aria-label="选择全部 SKU"]').setValue(true)
    await wrapper.get('[aria-label="选择全部 SKU"]').setValue(false)
    expect(wrapper.text()).toContain('已选 0 / 1')
  })
  it('applies values to empty cells directly and renders the default specification', async () => {
    const wrapper = setup([{ ...row(0), optionKeys: [], label: '', skuCode: '', priceYuan: '', gradePrices: {} }])
    await wrapper.setProps({ axes: [] })
    expect(wrapper.text()).toContain('默认规格')
    await wrapper.get('[aria-label="选择 SKU 默认规格"]').setValue(true)
    await batch(wrapper, '0')
    expect(confirmAction).not.toHaveBeenCalled()
    expect((wrapper.emitted('update:skus')!.at(-1)![0] as EditableSku[])[0]!.priceYuan).toBe('0')
  })
  it('renders read-only SKU state without batch controls', () => {
    const wrapper = setup([{ ...row(0), id: 'existing', saleStatus: 'ON_SALE' }], true)
    expect(wrapper.text()).toContain('保留现有 SKU')
    expect(wrapper.text()).toContain('已上架')
    expect(wrapper.find('[data-action="batch-price"]').exists()).toBe(false)
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.findAll('tbody input').every(input => input.attributes('disabled') !== undefined)).toBe(true)
  })
  it('discards a pending batch if rows or enabled grade settings change', async () => {
    let resolve!: (accepted: boolean) => void
    vi.mocked(confirmAction).mockImplementation(() => new Promise(done => { resolve = done }))
    const wrapper = setup()
    await wrapper.get('[aria-label="选择全部 SKU"]').setValue(true)
    await batch(wrapper, '8.00')
    await wrapper.setProps({ skus: [row(0)] })
    resolve(true)
    await flushPromises()
    expect(wrapper.emitted('update:skus')).toBeUndefined()
    await wrapper.get('[aria-label="批量价格字段"]').setValue('gold')
    await batch(wrapper, '8.00')
    await wrapper.setProps({ grades: [] })
    resolve(true)
    await flushPromises()
    expect(wrapper.emitted('update:skus')).toBeUndefined()
  })
  it('flags invalid grade prices and missing units while showing fallback names', async () => {
    const wrapper = setup([{ ...row(0), skuCode: '', label: '', baseUnit: '', saleUnit: '', gradePrices: { gold: 'bad' } }])
    await wrapper.setProps({ showErrors: true, axes: [{ clientKey: 'unknown', name: '', options: [] }] })
    expect(wrapper.text()).toContain('1 单位＝6 单位')
    expect(wrapper.findAll('[aria-invalid="true"]')).toHaveLength(2)
    await wrapper.get('[aria-label="选择 SKU 默认规格"]').setValue(true)
    expect(wrapper.text()).toContain('已选 1 / 1')
  })

})
