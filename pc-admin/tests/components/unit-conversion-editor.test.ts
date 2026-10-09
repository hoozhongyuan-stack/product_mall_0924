import { afterEach, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import UnitConversionEditor from '../../src/views/catalog/UnitConversionEditor.vue'
import type { EditableAxis } from '../../src/views/catalog/spec-editor'
import type { UnitConversion } from '../../src/views/catalog/types'
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
const axis: EditableAxis = { clientKey: 'u', name: '单位', options: [{ clientKey: 'b', value: '瓶' }, { clientKey: 'x', value: '箱' }] }
const conversion: UnitConversion = { axisKey: 'u', baseOptionKey: 'b', ratios: [{ optionKey: 'b', ratio: 1 }, { optionKey: 'x', ratio: 6 }] }
function setup(axes: EditableAxis[] = [axis], config: UnitConversion | null = conversion) {
  const w = mount(UnitConversionEditor, { props: { axes, conversion: config, disabled: false, showErrors: false } }); wrappers.push(w); return w
}
function change(w: ReturnType<typeof mount>) { return w.emitted('change')!.at(-1)![0] as { axes: EditableAxis[]; conversion: UnitConversion | null } }
it('initializes a stable unit axis and allows choosing base, adding and removing units', async () => {
  const w = setup([], null)
  await w.get('[aria-label="开启多单位换算"]').setValue(true)
  let next = change(w)
  expect(next.axes[0]!.name).toBe('单位')
  expect(next.conversion!.baseOptionKey).toBe(next.axes[0]!.options[0]!.clientKey)
  await w.setProps({ axes: [axis], conversion })
  await w.get('[aria-label="单位名称 2"]').setValue('整箱')
  expect(change(w).axes[0]!.options[1]!.clientKey).toBe('x')
  expect(axis.options[1]!.value).toBe('箱')
  await w.get('[aria-label="基本单位选择"]').setValue('x')
  next = change(w)
  expect(next.conversion!.ratios).toEqual([{ optionKey: 'b', ratio: 1 }, { optionKey: 'x', ratio: 1 }])
  await w.get('[aria-label="单位换算比 2"]').setValue(12)
  expect(change(w).conversion!.ratios[1]!.ratio).toBe(12)
  await w.findAll('button').find(b => b.text() === '添加单位')!.trigger('click')
  expect(change(w).axes[0]!.options).toHaveLength(3)
  await w.get('[aria-label="移除单位 箱"]').trigger('click')
  next = change(w)
  expect(next.axes[0]!.options.map(o => o.clientKey)).toEqual(['b'])
  expect(next.conversion!.ratios).toEqual([{ optionKey: 'b', ratio: 1 }])
  await w.get('[aria-label="开启多单位换算"]').setValue(false)
  expect(change(w)).toEqual({ axes: [], conversion: null })
})
it('reserves one specification axis, validates names and never edits disabled configuration', async () => {
  const other = { ...axis, clientKey: 'other', name: '度数' }
  const w = setup([axis, other], null)
  expect(w.get('[aria-label="开启多单位换算"]').attributes('disabled')).toBeDefined()
  expect(w.text()).toContain('请先移除一个规格项')
  await w.setProps({ axes: [{ ...axis, options: [{ clientKey: 'b', value: '' }, { clientKey: 'x', value: '' }] }], conversion, showErrors: true })
  expect(w.findAll('[aria-invalid="true"]')).toHaveLength(2)
  expect(w.text()).toContain('基本单位')
  await w.setProps({ disabled: true })
  await w.get('[aria-label="单位名称 2"]').trigger('input')
  expect(w.emitted('change')).toBeUndefined()
})
it('keeps other specifications unchanged when unit configuration is removed', async () => {
  const other = { clientKey: 'a', name: '度数', options: [{ clientKey: 'd', value: '53°' }] }
  const w = setup([other, axis])
  await w.get('[aria-label="开启多单位换算"]').setValue(false)
  expect(change(w)).toEqual({ axes: [other], conversion: null })
})
