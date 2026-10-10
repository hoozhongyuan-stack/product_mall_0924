import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PageContentFields from '../../src/views/pages/PageContentFields.vue'
import PageComponentEditor from '../../src/views/pages/PageComponentEditor.vue'
import { createComponent, normalizeConfig, type PageConfig } from '../../src/views/pages/types'
const targets = { categories: [], products: [], pages: [] }
const stubs = { AssetPicker: true, PageLinkEditor: true, HotzoneCanvas: true }
describe('controlled page content fields', () => {
  it('updates the title while preserving the selected component and immutable source', async () => {
    const component = createComponent('TITLE', 1)
    const wrapper = mount(PageContentFields, { props: { ...targets, component }, global: { stubs } })
    await wrapper.get('input').setValue('品牌推荐')
    const result = wrapper.emitted('change')?.[0]?.[0] as typeof component
    expect(result.componentId).toBe(component.componentId)
    expect(result.props.text).toBe('品牌推荐')
    expect(component.props.text).toBe('')
    wrapper.unmount()
  })
  it('blocks disabled navigation changes even if an event is dispatched directly', async () => {
    const component = createComponent('NAVIGATION', 1)
    const wrapper = mount(PageContentFields, { props: { ...targets, component, disabled: true }, global: { stubs } })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('change')).toBeUndefined()
    wrapper.unmount()
  })
  it('promotes appearance edits to schema 2 and deep clones appearance', async () => {
    const component = createComponent('NOTICE', 1)
    const wrapper = mount(PageComponentEditor, { props: { ...targets, component, canUpload: false }, global: { stubs } })
    await wrapper.get('input[type="color"]').setValue('#123456')
    const result = wrapper.emitted('change')?.[0]?.[0] as typeof component
    expect(result.appearance?.backgroundColor).toBe('#123456')
    const config: PageConfig = { schemaVersion: 1, pageType: 'HOME', theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#ffffff', brandTextColor: '#000000' }, components: [result] }
    const normalized = normalizeConfig(config)
    expect(normalized.schemaVersion).toBe(2)
    expect(normalized.components[0]?.appearance).not.toBe(result.appearance)
    wrapper.unmount()
  })
})
