import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import PageContentFields from '../../src/views/pages/PageContentFields.vue'
import PageComponentEditor from '../../src/views/pages/PageComponentEditor.vue'
import HomePagePreview from '../../src/views/pages/HomePagePreview.vue'
import EditorActions from '../../src/views/pages/EditorActions.vue'
import HotzoneCanvas from '../../src/views/pages/HotzoneCanvas.vue'
import PageCopyPanel from '../../src/views/pages/PageCopyPanel.vue'
import { useEditorRuntime } from '../../src/views/pages/editor-runtime'
import { applyUploadedAsset, createComponent, normalizeConfig, type ComponentType, type PageConfig } from '../../src/views/pages/types'
vi.mock('../../src/api', () => ({ api: vi.fn(), ApiError: class ApiError extends Error { constructor(message: string, public status: number, public code: string) { super(message) } } }))
import { api, ApiError } from '../../src/api'
const targets = { categories: [{ id: 'c1', name: '分类' }], products: [{ productId: 'p1', name: '商品' }], pages: [] }
const stubs = { AssetPicker: true, PageLinkEditor: true, HotzoneCanvas: true }
const base = (): PageConfig => ({ schemaVersion: 1, pageType: 'HOME', theme: { pageBackgroundColor: '#ffffff', headerBackgroundColor: '#ffffff', brandTextColor: '#111111' }, components: [] })
afterEach(() => { vi.resetAllMocks(); sessionStorage.clear() })
describe('page configuration interactions', () => {
  for (const type of ['TITLE', 'IMAGE', 'NAVIGATION', 'PRODUCT_LIST'] as ComponentType[]) {
    it(`edits ${type} fields without mutating input`, async () => {
      const component = createComponent(type, 1)
      if (type === 'NAVIGATION') component.props.items = [{ title: 'A', assetId: 'image', link: { type: 'FUNCTION', targetId: 'CATALOG' } }, { title: 'B', link: { type: 'FUNCTION', targetId: 'SEARCH' } }]
      const original = JSON.stringify(component)
      const wrapper = mount(PageContentFields, { props: { ...targets, component }, global: { stubs } })
      for (const input of wrapper.findAll('input')) await input.setValue(input.attributes('type') === 'number' ? '8' : '修改')
      for (const select of wrapper.findAll('select')) await select.setValue(select.attributes('multiple') !== undefined ? ['p1'] : select.findAll('option').at(-1)!.element.value)
      for (const textarea of wrapper.findAll('textarea')) await textarea.setValue('p1\np2')
      for (const link of wrapper.findAllComponents({ name: 'PageLinkEditor' })) { link.vm.$emit('update:model-value', undefined); link.vm.$emit('update:model-value', { type: 'FUNCTION', targetId: 'SEARCH' }) }
      for (const asset of wrapper.findAllComponents({ name: 'AssetPicker' })) asset.vm.$emit('select', { assetId: 'chosen' })
      for (const button of wrapper.findAll('button')) await button.trigger('click')
      if (type === 'PRODUCT_LIST') { await wrapper.setProps({ component: { ...component, props: { ...component.props, source: 'CATEGORY' } } }); await wrapper.find('select').setValue('CATEGORY'); await wrapper.findAll('select')[1]!.setValue('c1') }
      expect(JSON.stringify(component)).toBe(original)
      expect(wrapper.emitted('change')?.length).toBeGreaterThan(0)
      wrapper.unmount()
    })
  }
  it('binds compatible upload slots and rejects removed or changed positions', () => {
    const image = createComponent('IMAGE_HOTZONE', 1), carousel = createComponent('CAROUSEL', 2)
    carousel.props.slides = [{ assetId: 'old' }]
    const config = { ...base(), components: [image, carousel] }
    expect(applyUploadedAsset(config, { componentId: 'removed', type: 'IMAGE', slot: 'image', expectedAssetId: '', assetId: 'new' })).toBe(config)
    expect(applyUploadedAsset(config, { componentId: image.componentId, type: 'IMAGE', slot: 'image', expectedAssetId: '', assetId: 'new' })).toBe(config)
    expect(applyUploadedAsset(config, { componentId: carousel.componentId, type: 'CAROUSEL', slot: 8, expectedSlides: '[]', assetId: 'new' })).toBe(config)
    expect(applyUploadedAsset(config, { componentId: carousel.componentId, type: 'CAROUSEL', slot: 0, expectedSlides: '[]', assetId: 'new' })).toBe(config)
    const next = applyUploadedAsset(config, { componentId: carousel.componentId, type: 'CAROUSEL', slot: 0, expectedSlides: JSON.stringify(carousel.props.slides), assetId: 'new' })
    expect(next.components[1]?.props.slides?.[0]?.assetId).toBe('new')
    expect(applyUploadedAsset(config, { componentId: image.componentId, type: 'IMAGE_HOTZONE', slot: 'hotzone', expectedAssetId: '', assetId: 'new' }).components[0]?.props.assetId).toBe('new')
    expect(normalizeConfig({ ...config, schemaVersion: 2 }).schemaVersion).toBe(2)
  })
  it('exercises the action controls through disabled and available states', async () => {
    const wrapper = mount(EditorActions, { props: { disabled: false, canUndo: true, canRedo: true, canCopy: true, count: 1 } })
    for (const button of wrapper.findAll('button')) await button.trigger('click')
    expect(wrapper.emitted('undo')).toHaveLength(1); expect(wrapper.emitted('redo')).toHaveLength(1); expect(wrapper.emitted('copy')).toHaveLength(1)
    await wrapper.setProps({ disabled: true, count: 40, canUndo: false, canRedo: false, canCopy: false })
    expect(wrapper.findAll('button').slice(0, 3).every(button => button.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })
})
describe('runtime deployment fence', () => {
  for (const value of [1, 2, 3, 4, 5, 'failed']) it(`handles runtime ${value}`, async () => {
    if (value === 'failed') vi.mocked(api).mockRejectedValue(new Error('offline'))
    else vi.mocked(api).mockResolvedValue({ runtimeSchemaVersion: value })
    const editor = ref<PageConfig | null>({ ...base(), schemaVersion: 2 })
    let runtime!: ReturnType<typeof useEditorRuntime>
    const wrapper = mount(defineComponent({ setup() { runtime = useEditorRuntime(editor); return () => null } }))
    await flushPromises()
    expect(runtime.runtimeBlocked.value).toBe(value !== 2 && value !== 3 && value !== 4)
    editor.value = base(); expect(runtime.runtimeBlocked.value).toBe(false)
    await runtime.loadRuntime(); expect(runtime.runtimeLoading.value).toBe(false)
    wrapper.unmount()
  })
})
describe('configuration editor input and media errors', () => {
  for (const type of ['SEARCH', 'NOTICE', 'CAROUSEL', 'IMAGE_HOTZONE', 'IMAGE', 'DIVIDER', 'FILING'] as ComponentType[]) {
    it(`updates ${type} legacy and media controls`, async () => {
      const component = createComponent(type, 1)
      if (type === 'CAROUSEL') component.props.slides = [{ assetId: 'a' }, { assetId: 'b' }]
      if (type === 'IMAGE_HOTZONE') { component.props.assetId = 'a'; component.props.areas = [{ x: .1, y: .1, width: .3, height: .3, link: { type: 'FUNCTION', targetId: 'CATALOG' } }] }
      const wrapper = mount(PageComponentEditor, { props: { ...targets, component, canUpload: true }, global: { stubs } })
      for (const input of wrapper.findAll('input:not([type="file"])')) await input.setValue(input.attributes('type') === 'number' ? '.2' : input.attributes('type') === 'color' ? '#223344' : '内容')
      for (const textarea of wrapper.findAll('textarea')) await textarea.setValue('公告')
      for (const select of wrapper.findAll('select')) await select.setValue(select.findAll('option').at(-1)!.element.value)
      for (const link of wrapper.findAllComponents({ name: 'PageLinkEditor' })) link.vm.$emit('update:model-value', { type: 'FUNCTION', targetId: 'SEARCH' })
      for (const asset of wrapper.findAllComponents({ name: 'AssetPicker' })) asset.vm.$emit('select', { assetId: 'new-image' })
      for (const button of wrapper.findAll('button')) await button.trigger('click')
      if (['CAROUSEL', 'IMAGE_HOTZONE', 'IMAGE'].includes(type)) expect(wrapper.emitted('uploaded')?.length).toBeGreaterThan(0)
      expect(wrapper.emitted('change')?.length).toBeGreaterThan(0)
      await wrapper.setProps({ disabled: true }); for (const asset of wrapper.findAllComponents({ name: 'AssetPicker' })) asset.vm.$emit('select', { assetId: 'ignored' })
      wrapper.unmount()
    })
  }
  it('validates files, retains original slot on success and reports failed uploads', async () => {
    const component = createComponent('IMAGE', 1)
    const wrapper = mount(PageComponentEditor, { props: { ...targets, component, canUpload: true }, global: { stubs } })
    async function upload(file: File | undefined) { const input = wrapper.get('input[type="file"]'); Object.defineProperty(input.element, 'files', { configurable: true, value: file ? [file] : [] }); await input.trigger('change'); await flushPromises() }
    await upload(undefined)
    await upload(new File(['bad'], 'bad.txt', { type: 'text/plain' })); expect(wrapper.text()).toContain('JPG 或 PNG')
    vi.mocked(api).mockResolvedValue({ assetId: 'new' }); await upload(new File(['image'], 'a.png', { type: 'image/png' })); expect(wrapper.emitted('uploaded')?.[0]?.[0]).toMatchObject({ type: 'IMAGE', slot: 'image', assetId: 'new' })
    vi.mocked(api).mockRejectedValue(new Error('上传连接失败')); await upload(new File(['image'], 'a.png', { type: 'image/png' })); expect(wrapper.text()).toContain('上传连接失败')
    vi.mocked(api).mockRejectedValue('fail'); await upload(new File(['image'], 'a.png', { type: 'image/png' })); expect(wrapper.text()).toContain('图片上传失败')
    wrapper.unmount()
  })
})
describe('hotzone pointer and keyboard gestures', () => {
  it('draws, moves, resizes, cancels stale gestures and blocks disabled input', async () => {
    const area = { x: .1, y: .1, width: .3, height: .3, link: { type: 'FUNCTION' as const, targetId: 'CATALOG' } }
    const wrapper = mount(HotzoneCanvas, { props: { assetId: 'image', areas: [area] } })
    const surface = wrapper.get('.hotzone-canvas')
    Object.defineProperty(surface.element, 'getBoundingClientRect', { value: () => ({ left: 0, top: 0, width: 100, height: 100 }) })
    Object.defineProperty(surface.element, 'setPointerCapture', { value: vi.fn() })
    const vm = wrapper.vm as unknown as { start: (e: PointerEvent, index?: number, mode?: string) => void; move: (e: PointerEvent) => void; finish: (e: PointerEvent) => void; cancel: () => void }
    const event = (x: number, y: number, pointerId = 1) => ({ clientX: x, clientY: y, pointerId, button: 0, preventDefault: vi.fn() }) as unknown as PointerEvent
    vm.move(event(20, 20)); vm.finish(event(20, 20))
    vm.start(event(50, 50)); vm.move(event(80, 80, 2)); vm.finish(event(80, 80))
    expect(wrapper.emitted('change')?.[0]?.[0]).toHaveLength(2)
    vm.start(event(10, 10), 0, 'move'); vm.finish(event(30, 30))
    expect((wrapper.emitted('change')?.[1]?.[0] as typeof area[])[0]?.x).toBe(.3)
    vm.start(event(40, 40), 0, 'resize'); vm.finish(event(60, 60))
    expect((wrapper.emitted('change')?.[2]?.[0] as typeof area[])[0]?.width).toBe(.5)
    const count = wrapper.emitted('change')!.length
    vm.start(event(10, 10), 0, 'move'); await wrapper.setProps({ areas: [{ ...area, x: .2 }] }); vm.finish(event(50, 50))
    expect(wrapper.emitted('change')).toHaveLength(count)
    const frame = wrapper.get('.hotzone-frame')
    await frame.trigger('focus')
    for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Escape']) await frame.trigger('keydown', { key, shiftKey: key === 'ArrowRight' })
    expect(wrapper.emitted('change')).toHaveLength(count + 4)
    await wrapper.setProps({ disabled: true }); vm.start(event(10, 10)); await frame.trigger('keydown', { key: 'ArrowLeft' }); expect(wrapper.emitted('change')).toHaveLength(count + 4)
    await wrapper.setProps({ disabled: false, areas: Array.from({ length: 20 }, () => area) }); vm.start(event(50, 50)); vm.finish(event(90, 90)); expect(wrapper.emitted('change')).toHaveLength(count + 4)
    vm.cancel(); await wrapper.get('img').trigger('error'); expect(wrapper.text()).toContain('图片无法读取')
    await wrapper.setProps({ assetId: '' }); expect(wrapper.find('.hotzone-canvas').exists()).toBe(false)
    wrapper.unmount()
  })
})
describe('published and immediate canvas rendering', () => {
  it('renders all configured branches, image failures and carousel selection without navigating', async () => {
    const components = (['SEARCH', 'NOTICE', 'CAROUSEL', 'IMAGE_HOTZONE', 'DIVIDER', 'FILING', 'TITLE', 'IMAGE', 'NAVIGATION', 'PRODUCT_LIST'] as ComponentType[]).map((type, index) => createComponent(type, index + 1))
    components[1]!.props = { text: '公告', link: { type: 'FUNCTION', targetId: 'CATALOG' } }
    components[2]!.props.slides = [{ assetId: 'first' }, { assetId: 'second' }]
    components[3]!.props = { assetId: 'zone', areas: [{ x: .1, y: .1, width: .3, height: .3, link: { type: 'FUNCTION', targetId: 'SEARCH' } }] }
    components[6]!.props = { text: '标题', subtitle: '副标题', size: 24, align: 'CENTER' }
    components[7]!.props = { assetId: 'ad', ratio: '1:1' }
    components[8]!.props = { columns: 2, items: [{ title: '分类', assetId: 'nav', link: { type: 'FUNCTION', targetId: 'CATALOG' } }] }
    components[0]!.appearance = { backgroundColor: '#223344', padding: 8, margin: 4, radius: 8 }
    const productId = components[9]!.componentId
    const wrapper = mount(HomePagePreview, { props: { config: { ...base(), components }, stale: true, pageName: '专题', componentData: { [productId]: [{ productId: 'p1', name: '商品', priceFen: 100, imageUrl: '/image', purchasable: true }] } } })
    expect(wrapper.text()).toContain('副标题'); expect(wrapper.text()).toContain('1.00')
    await wrapper.get('.preview-carousel-next').trigger('click'); expect(wrapper.text()).toContain('2 / 2')
    for (const img of wrapper.findAll('img')) await img.trigger('error')
    expect(wrapper.text()).toContain('图片无法读取')
    const empty = components.map(item => ({ ...item, props: {} }))
    await wrapper.setProps({ config: { ...base(), pageType: 'MICRO', components: empty }, interactive: true, selectedId: empty[0]!.componentId })
    await wrapper.get('.preview-component').trigger('click'); await wrapper.get('.preview-component').trigger('keydown', { key: ' ' }); await wrapper.get('.preview-component').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('select')).toHaveLength(2)
    expect(wrapper.text()).toContain('添加图文导航')
    await wrapper.setProps({ config: { ...base(), components: [] } }); expect(wrapper.text()).toContain('没有显示')
    wrapper.unmount()
  })
})
describe('copy failure boundaries', () => {
  const properties = { accountId: 'a', pageId: 'source', name: '源页面', revision: 3, publishedRevision: 2 }
  it('blocks corrupt restoration records and rejects empty names', async () => {
    sessionStorage.setItem('mall-page-copy:a', '{broken')
    const corrupt = mount(PageCopyPanel, { props: properties }); expect(corrupt.text()).toContain('无法读取'); corrupt.unmount()
    sessionStorage.clear()
    const wrapper = mount(PageCopyPanel, { props: properties })
    await wrapper.get('button').trigger('click'); await wrapper.get('input').setValue(' '); await wrapper.get('form').trigger('submit')
    expect(wrapper.text()).toContain('1—80')
    await wrapper.get('select').setValue('PUBLISHED'); await wrapper.get('input').setValue('线上副本')
    vi.mocked(api).mockRejectedValue(new ApiError('源页已变化', 409, 'REVISION_CONFLICT'))
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('重新读取源页面'); expect(sessionStorage.getItem('mall-page-copy:a')).toBeNull()
    await wrapper.findAll('button').find(button => button.text() === '取消')?.trigger('click')
    wrapper.unmount()
  })
  it('retains identity for incomplete success results and unconfirmed failures', async () => {
    const wrapper = mount(PageCopyPanel, { props: properties })
    await wrapper.get('button').trigger('click')
    vi.mocked(api).mockResolvedValue({ pageId: 'source', publishedRevision: null })
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('结果尚未确认')
    const stored = sessionStorage.getItem('mall-page-copy:a')
    vi.mocked(api).mockResolvedValue({ pageId: 'new', publishedRevision: null })
    await wrapper.findAll('button').find(button => button.text() === '恢复原复制请求')!.trigger('click'); await flushPromises()
    expect(stored).toContain('源页面'); expect(wrapper.emitted('copied')).toHaveLength(1)
    wrapper.unmount()
  })
})
