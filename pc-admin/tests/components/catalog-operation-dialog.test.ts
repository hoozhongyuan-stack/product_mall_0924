import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect } from 'vitest'
import { ElDialog } from 'element-plus'
import CatalogOperationDialog from '../../src/views/catalog/CatalogOperationDialog.vue'

describe('catalog operation dialog', () => {
  it('locks outside dismissal and guards closing while busy, with a fixed footer', async () => {
    const wrapper = mount(CatalogOperationDialog, { props: { title: '商品操作', busy: false }, slots: { default: '操作内容', footer: '<button>保存</button>' } })
    await flushPromises()
    const dialog = wrapper.getComponent(ElDialog)
    expect(dialog.props('closeOnClickModal')).toBe(false)
    expect(dialog.props('closeOnPressEscape')).toBe(true)
    expect(dialog.props('width')).toBe('760px')
    expect(wrapper.get('.el-dialog__footer').text()).toBe('保存')
    dialog.props('beforeClose')!(() => {})
    expect(wrapper.emitted('close')).toHaveLength(1)
    await wrapper.setProps({ busy: true, wide: true })
    expect(dialog.props('closeOnPressEscape')).toBe(false)
    expect(dialog.props('showClose')).toBe(false)
    expect(dialog.props('width')).toBe('1120px')
    dialog.props('beforeClose')!(() => {})
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})
