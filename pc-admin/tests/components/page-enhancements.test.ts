import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { applyUploadedAsset, createComponent, normalizeConfig, type PageConfig } from '../../src/views/pages/types'
import HomePagePreview from '../../src/views/pages/HomePagePreview.vue'
const base = (): PageConfig => ({ schemaVersion: 1, pageType: 'MICRO', theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#ffffff', brandTextColor: '#000000' }, components: [] })
describe('page component evolution', () => {
  it('keeps legacy schema and promotes new components without losing content', () => {
    expect(normalizeConfig(base()).schemaVersion).toBe(1)
    const config = { ...base(), components: [createComponent('TITLE', 1)] }
    expect(normalizeConfig(config).schemaVersion).toBe(2)
    expect(normalizeConfig({ ...base(), schemaVersion: 2 }).schemaVersion).toBe(2)
  })
  it('rejects late image upload after its image slot changed', () => {
    const image = createComponent('IMAGE', 1)
    image.props.assetId = 'new-image'
    const config = { ...base(), components: [image] }
    expect(applyUploadedAsset(config, { componentId: image.componentId, type: 'IMAGE', slot: 'image', assetId: 'late', expectedAssetId: '' })).toBe(config)
    const next = applyUploadedAsset(config, { componentId: image.componentId, type: 'IMAGE', slot: 'image', assetId: 'chosen', expectedAssetId: 'new-image' })
    expect(next.components[0]?.props.assetId).toBe('chosen')
    expect(config.components[0]?.props.assetId).toBe('new-image')
  })
  it('selects by keyboard and renders supplied real product prices', async () => {
    const product = createComponent('PRODUCT_LIST', 1)
    const wrapper = mount(HomePagePreview, { props: { config: { ...base(), components: [product] }, stale: false, interactive: false, componentData: { [product.componentId]: [{ productId: 'p1', name: '真实商品', priceFen: 1234, imageUrl: '', purchasable: false }] } } })
    expect(wrapper.text()).toContain('12.34')
    expect(wrapper.text()).toContain('真实商品')
    await wrapper.setProps({ interactive: true })
    expect(wrapper.text()).not.toContain('真实商品')
    await wrapper.get('[role="button"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('select')?.[0]).toEqual([product.componentId])
    wrapper.unmount()
  })
  it('renders title and navigation safely as text with no live links', () => {
    const title = createComponent('TITLE', 1)
    title.props.text = '<script>bad()</script>'
    const nav = createComponent('NAVIGATION', 2)
    nav.props.items = [{ title: '分类入口', link: { type: 'FUNCTION', targetId: 'CATALOG' } }]
    const wrapper = mount(HomePagePreview, { props: { config: { ...base(), components: [title, nav] }, stale: false } })
    expect(wrapper.text()).toContain('<script>bad()</script>')
    expect(wrapper.text()).toContain('分类入口')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('a[href]').exists()).toBe(false)
    wrapper.unmount()
  })
})
