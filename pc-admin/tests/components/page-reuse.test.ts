import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PageReusePanel from '../../src/views/pages/PageReusePanel.vue'
import PageMetadataEditor from '../../src/views/pages/PageMetadataEditor.vue'
import HomePagePreview from '../../src/views/pages/HomePagePreview.vue'
import PageComponentEditor from '../../src/views/pages/PageComponentEditor.vue'
import { validateReuseData } from '../../src/views/pages/page-reuse'
import MosaicFields from '../../src/views/pages/MosaicFields.vue'
import { createComponent, normalizeConfig, type PageConfig } from '../../src/views/pages/types'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
import { api } from '../../src/api'
const config = (): PageConfig => ({ schemaVersion: 2, pageType: 'MICRO', theme: { pageBackgroundColor: '#fff5e8', headerBackgroundColor: '#b63f32', brandTextColor: '#fff8ef' }, components: [createComponent('TITLE', 1)] })
const payload = () => ({ templates: [{ templateId: 'BRAND_HOME', version: 1, name: '品牌首页', description: '品牌展示', config: { ...config(), schemaVersion: 3 } }], combinations: [{ combinationId: 'BRAND_HEADER', version: 1, name: '品牌头部', description: '品牌组合', components: config().components }] })
afterEach(() => vi.resetAllMocks())
describe('phase two page reuse', () => {
  it('keeps metadata and old schema, promotes mosaic and spacer to schema 3', () => {
    expect(normalizeConfig(config()).schemaVersion).toBe(2)
    expect(() => normalizeConfig({ ...config(), schemaVersion: 5 } as never)).toThrow('更新')
    const next = { ...config(), metadata: { tags: ['品牌'], share: { title: '分享', description: '', coverAssetId: '' } } }
    expect(normalizeConfig(next).schemaVersion).toBe(3)
    expect(normalizeConfig(next).metadata).toEqual(next.metadata)
    expect(normalizeConfig({ ...config(), components: [createComponent('MOSAIC', 1)] }).schemaVersion).toBe(3)
    expect(createComponent('SPACER', 1).props.height).toBe(16)
  })
  it('requires explicit template confirmation and clones component identities', async () => {
    const data = payload(); vi.mocked(api).mockResolvedValue(data)
    const original = config()
    const wrapper = mount(PageReusePanel, { props: { config: original } }); await flushPromises()
    await wrapper.findAll('button').find(button => button.text().includes('品牌首页'))!.trigger('click')
    expect(wrapper.emitted('replace')).toBeUndefined()
    expect(wrapper.get('[role="alertdialog"]').text()).toContain('替换所有当前组件和主题')
    await wrapper.findAll('button').find(button => button.text() === '确认替换草稿')!.trigger('click')
    const result = wrapper.emitted('replace')![0]![0] as PageConfig
    expect(result.pageType).toBe('MICRO')
    expect(result.components[0]?.componentId).not.toBe(data.templates[0]?.config.components[0]?.componentId)
    expect(result.components[0]?.props).not.toBe(data.templates[0]?.config.components[0]?.props)
    wrapper.unmount()
  })
  it('de-duplicates tags and preserves share values on controlled changes', async () => {
    const metadata = { tags: [], share: { title: '标题', description: '简介', coverAssetId: '' } }
    const wrapper = mount(PageMetadataEditor, { props: { metadata }, global: { stubs: { AssetPicker: true } } })
    await wrapper.get('input[data-field="tags"]').setValue('品牌, 品牌,活动, 分类')
    expect(wrapper.emitted('change')![0]![0]).toEqual({ ...metadata, tags: ['品牌', '活动', '分类'] })
    expect(wrapper.text()).toContain('微信消息仅支持标题和封面')
    wrapper.unmount()
  })
  it('changes mosaic template while preserving slot data and warning about truncation', async () => {
    const component = createComponent('MOSAIC', 1)
    component.props.template = 'FOUR'; component.props.items = Array.from({ length: 4 }, (_, index) => ({ assetId: `asset-${index}`, title: String(index) }))
    const wrapper = mount(MosaicFields, { props: { component, categories: [], products: [], pages: [] }, global: { stubs: { AssetPicker: true, PageLinkEditor: true } } })
    await wrapper.get('select').setValue('TWO')
    expect(wrapper.emitted('change')).toBeUndefined()
    expect(wrapper.text()).toContain('最后 2 个位置')
    await wrapper.findAll('button').find(button => button.text() === '确认切换模板')!.trigger('click')
    const next = wrapper.emitted('change')![0]![0] as typeof component
    expect(next.props.items?.map(item => item.assetId)).toEqual(['asset-0', 'asset-1'])
    wrapper.unmount()
  })
})
describe('reuse panel failure and controlled boundaries', () => {
  it('retries load failures, appends independent groups and preserves metadata when applying a theme', async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error('网络错误')).mockResolvedValue(payload())
    const current = { ...config(), metadata: { tags: ['品牌'], share: { title: '标题', description: '说明', coverAssetId: '' } } }
    const wrapper = mount(PageReusePanel, { props: { config: current } }); await flushPromises()
    expect(wrapper.text()).toContain('网络错误')
    await wrapper.findAll('button').find(button => button.text() === '重试读取模板')!.trigger('click'); await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '加入品牌头部组合')!.trigger('click')
    const group = wrapper.emitted('append')![0]![0] as typeof current.components
    expect(group[0]?.sortOrder).toBe(2); expect(group[0]?.componentId).not.toBe(current.components[0]?.componentId)
    for (const name of ['品牌红', '清爽白', '深色']) await wrapper.findAll('button').find(button => button.text() === name)!.trigger('click')
    const themeResult = wrapper.emitted('replace')![2]![0] as PageConfig
    expect(themeResult.metadata).toEqual(current.metadata); expect(themeResult.components).toEqual(current.components)
    expect(themeResult.theme.pageBackgroundColor).toBe('#F4F5F5')
    await wrapper.findAll('button').find(button => button.text() === '应用品牌首页模板')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === '取消')!.trigger('click'); expect(wrapper.find('[role="alertdialog"]').exists()).toBe(false)
    await wrapper.setProps({ disabled: true }); expect(wrapper.findAll('button').filter(button => button.text() !== '重试读取模板').every(button => button.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })
  it('rejects malformed template contracts and bounds combinations to forty', async () => {
    expect(() => validateReuseData({ templates: [] } as never)).toThrow()
    expect(() => validateReuseData({ ...payload(), templates: [{ ...payload().templates[0]!, version: 2 }] } as never)).toThrow()
    expect(() => validateReuseData({ ...payload(), combinations: [{ ...payload().combinations[0]!, combinationId: 'bad' }] } as never)).toThrow()
    vi.mocked(api).mockResolvedValue(payload())
    const current = config(); current.components = Array.from({ length: 40 }, (_, index) => createComponent('TITLE', index + 1))
    const wrapper = mount(PageReusePanel, { props: { config: current } }); await flushPromises()
    expect(wrapper.findAll('button').find(button => button.text().startsWith('加入'))!.attributes('disabled')).toBeDefined()
    const vm = wrapper.vm as unknown as { append: (item: ReturnType<typeof payload>['combinations'][number]) => void; choose: (item: ReturnType<typeof payload>['templates'][number]) => void; replace: () => void; preset: (index: number) => void }
    vm.append(payload().combinations[0]!); expect(wrapper.emitted('append')).toBeUndefined()
    await flushPromises(); expect(wrapper.text()).toContain('超过 40')
    vm.replace(); await wrapper.setProps({ disabled: true }); vm.choose(payload().templates[0]!); vm.replace(); vm.preset(0); vm.append(payload().combinations[0]!)
    expect(wrapper.emitted('replace')).toBeUndefined()
    wrapper.unmount()
  })
  it('discards a late template response after unmount and reports non-error failure', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(api).mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const wrapper = mount(PageReusePanel, { props: { config: config() } }); wrapper.unmount(); resolve(payload()); await flushPromises()
    vi.mocked(api).mockRejectedValue('offline')
    const second = mount(PageReusePanel, { props: { config: config() } }); await flushPromises(); expect(second.text()).toContain('模板暂不可用'); second.unmount()
  })
})
describe('metadata validation and media control', () => {
  it('limits tags while retaining valid share details and prevents disabled writes', async () => {
    const wrapper = mount(PageMetadataEditor, { global: { stubs: { AssetPicker: true } } })
    const tags = wrapper.get('[data-field="tags"]')
    await tags.setValue('a,b,c,d,e,f'); expect(wrapper.text()).toContain('标签未应用')
    expect((tags.element as HTMLInputElement).value).toBe('')
    expect(wrapper.emitted('change')).toBeUndefined()
    await tags.setValue('x'.repeat(21)); expect(wrapper.emitted('change')).toBeUndefined()
    await tags.setValue('a，a,b'); expect(wrapper.emitted('change')![0]![0]).toMatchObject({ tags: ['a', 'b'] })
    for (const input of wrapper.findAll('input').slice(1)) await input.setValue('资料')
    await wrapper.get('textarea').setValue('分享简介')
    wrapper.findComponent({ name: 'AssetPicker' }).vm.$emit('select', { assetId: 'cover' })
    expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ share: { coverAssetId: 'cover' } })
    await wrapper.setProps({ metadata: { tags: ['a'], share: { title: 't', description: 'd', coverAssetId: 'cover' } } })
    await wrapper.get('button').trigger('click'); expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ share: { coverAssetId: '' } })
    const count = wrapper.emitted('change')!.length
    await wrapper.setProps({ disabled: true }); await tags.setValue('blocked'); wrapper.findComponent({ name: 'AssetPicker' }).vm.$emit('select', { assetId: 'blocked' })
    expect(wrapper.emitted('change')).toHaveLength(count)
    wrapper.unmount()
  })
})
describe('mosaic and spacer design interactions', () => {
  it('grows slots without destroying existing selections, edits slot content and cancels truncation', async () => {
    const component = createComponent('MOSAIC', 1)
    const wrapper = mount(MosaicFields, { props: { component, categories: [], products: [], pages: [] }, global: { stubs: { AssetPicker: true, PageLinkEditor: true } } })
    await wrapper.get('select').setValue('FOUR')
    const four = wrapper.emitted('change')![0]![0] as typeof component
    expect(four.props.items).toHaveLength(4)
    await wrapper.setProps({ component: four })
    await wrapper.findAll('select')[1]!.setValue('16')
    for (const input of wrapper.findAll('input')) await input.setValue('素材配置')
    for (const asset of wrapper.findAllComponents({ name: 'AssetPicker' })) asset.vm.$emit('select', { assetId: 'picked' })
    for (const link of wrapper.findAllComponents({ name: 'PageLinkEditor' })) link.vm.$emit('update:model-value', { type: 'FUNCTION', targetId: 'SEARCH' })
    expect(wrapper.emitted('change')!.at(-1)![0]).toMatchObject({ props: { items: expect.arrayContaining([expect.objectContaining({ link: { type: 'FUNCTION', targetId: 'SEARCH' } })]) } })
    await wrapper.get('select').setValue('THREE'); await wrapper.findAll('button').find(button => button.text() === '取消')!.trigger('click'); expect(wrapper.find('[role="alertdialog"]').exists()).toBe(false)
    expect((wrapper.get('select').element as HTMLSelectElement).value).toBe('FOUR')
    const count = wrapper.emitted('change')!.length
    await wrapper.setProps({ disabled: true }); wrapper.findComponent({ name: 'AssetPicker' }).vm.$emit('select', { assetId: 'blocked' }); expect(wrapper.emitted('change')).toHaveLength(count)
    wrapper.unmount()
  })
  it('renders selected fixed mosaic layouts and spacer height, including image failure state', async () => {
    const mosaic = createComponent('MOSAIC', 1); mosaic.props.template = 'FEATURED'; mosaic.props.items = [{ assetId: 'a', title: '左图' }, { assetId: '' }, { assetId: 'b' }]
    const spacer = createComponent('SPACER', 2)
    const wrapper = mount(HomePagePreview, { props: { config: { ...config(), components: [mosaic, spacer] }, stale: false } })
    expect(wrapper.get('.preview-mosaic').classes()).toContain('preview-mosaic-FEATURED'); expect(wrapper.text()).toContain('左图')
    await wrapper.get('img').trigger('error'); expect(wrapper.text()).toContain('图片不可用')
    expect(wrapper.get('.preview-spacer').attributes('style')).toContain('16px')
    await wrapper.setProps({ config: { ...config(), components: [{ ...mosaic, props: {} }, { ...spacer, props: {} }] } }); expect(wrapper.get('.preview-mosaic').classes()).toContain('preview-mosaic-TWO')
    wrapper.unmount()
    const editor = mount(PageComponentEditor, { props: { component: spacer, canUpload: false, categories: [], products: [], pages: [] }, global: { stubs: { AssetPicker: true, PageLinkEditor: true } } })
    await editor.get('select').setValue('96'); expect(editor.emitted('change')![0]![0]).toMatchObject({ props: { height: 96 } }); editor.unmount()
  })
})
