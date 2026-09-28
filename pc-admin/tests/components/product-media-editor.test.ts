import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProductMediaEditor from '../../src/views/catalog/ProductMediaEditor.vue'
import { api } from '../../src/api'
import type { Asset } from '../../src/views/catalog/types'

vi.mock('../../src/api', async importOriginal => ({ ...await importOriginal<typeof import('../../src/api')>(), api: vi.fn() }))
const asset = (id: string, kind: 'IMAGE' | 'VIDEO' = 'IMAGE'): Asset => ({ assetId: id, kind, contentType: kind === 'IMAGE' ? 'image/png' : 'video/mp4', byteSize: 4, width: 800, height: 800, adminUrl: `/${id}` })
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.mocked(api).mockReset(); vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 800, height: 800, close() {} }))) })
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
function setup(overrides: Record<string, unknown> = {}) {
  const wrapper = mount(ProductMediaEditor, { props: { mainImage: null, galleryImages: [], video: null, targetKey: 'product1', canUpload: true, ...overrides,
    'onUpdate:mainImage': value => { void wrapper.setProps({ mainImage: value }) },
    'onUpdate:galleryImages': value => { void wrapper.setProps({ galleryImages: value }) },
    'onUpdate:video': value => { void wrapper.setProps({ video: value }) },
  }, global: { stubs: { AssetPicker: { name: 'AssetPicker', props: ['disabled', 'targetKey', 'excludedIds', 'kind', 'square'], template: '<button :disabled="disabled">素材中心</button>' } } } })
  wrappers.push(wrapper)
  return wrapper
}
async function upload(wrapper: ReturnType<typeof mount>, role: 'main' | 'gallery' | 'video' = 'main') {
  const group = wrapper.findAll('.catalog-media-group')[role === 'main' ? 0 : role === 'gallery' ? 1 : 2]!
  const input = group.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [new File(['data'], role === 'video' ? 'v.mp4' : 'i.png', { type: role === 'video' ? 'video/mp4' : 'image/png' })], configurable: true })
  await input.trigger('change')
  await flushPromises()
}
const pickers = (wrapper: ReturnType<typeof mount>) => wrapper.findAllComponents({ name: 'AssetPicker' })

describe('controlled product media editor', () => {
  it('adds, replaces and removes gallery assets without mutating the input array', async () => {
    const original = [asset('a')]
    const wrapper = setup({ galleryImages: original })
    pickers(wrapper)[2]!.vm.$emit('select', asset('b'))
    await flushPromises()
    expect(wrapper.props('galleryImages').map((a: Asset) => a.assetId)).toEqual(['a', 'b'])
    expect(original.map(a => a.assetId)).toEqual(['a'])
    pickers(wrapper)[1]!.vm.$emit('select', asset('c'))
    await flushPromises()
    expect(wrapper.props('galleryImages').map((a: Asset) => a.assetId)).toEqual(['c', 'b'])
    await wrapper.findAll('.catalog-media-group')[1]!.findAll('button').find(b => b.text() === '移除')!.trigger('click')
    expect(wrapper.props('galleryImages').map((a: Asset) => a.assetId)).toEqual(['b'])
  })
  it('allows library selection without upload permission and removes main/video independently', async () => {
    const wrapper = setup({ canUpload: false })
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    pickers(wrapper)[0]!.vm.$emit('select', asset('cover'))
    pickers(wrapper)[2]!.vm.$emit('select', asset('video', 'VIDEO'))
    await flushPromises()
    expect(wrapper.get('img').attributes('src')).toBe('/cover')
    expect(wrapper.get('video').attributes('src')).toBe('/video')
    for (const button of wrapper.findAll('button').filter(b => b.text() === '移除')) await button.trigger('click')
    expect(wrapper.props('mainImage')).toBeNull()
    expect(wrapper.props('video')).toBeNull()
  })
  it('rejects duplicate cover/gallery assets and hides the add slot at eight images', async () => {
    const wrapper = setup({ mainImage: asset('cover'), galleryImages: [asset('a')] })
    pickers(wrapper)[2]!.vm.$emit('select', asset('cover'))
    await flushPromises()
    expect(wrapper.props('galleryImages')).toHaveLength(1)
    expect(wrapper.text()).toContain('此图片已用于主图或其他附图')
    pickers(wrapper)[0]!.vm.$emit('select', asset('a'))
    await flushPromises()
    expect((wrapper.props('mainImage') as Asset).assetId).toBe('cover')
    await wrapper.setProps({ galleryImages: Array.from({ length: 8 }, (_, i) => asset(`g${i}`)) })
    expect(wrapper.find('.catalog-media-add').exists()).toBe(false)
  })
  it('rejects mismatched library types and non-square main images', async () => {
    const wrapper = setup()
    pickers(wrapper)[0]!.vm.$emit('select', asset('video', 'VIDEO'))
    await flushPromises()
    expect(wrapper.text()).toContain('素材类型不匹配')
    pickers(wrapper)[0]!.vm.$emit('select', { ...asset('wide'), height: 400 })
    await flushPromises()
    expect(wrapper.text()).toContain('正方形图片')
    expect(wrapper.props('mainImage')).toBeNull()
  })
  it('disables uploads, removals and library bindings while disabled', async () => {
    const wrapper = setup({ disabled: true, mainImage: asset('a') })
    expect(wrapper.findAll('input[type="file"]').every(i => (i.element as HTMLInputElement).disabled)).toBe(true)
    pickers(wrapper)[0]!.vm.$emit('select', asset('b'))
    await flushPromises()
    expect((wrapper.props('mainImage') as Asset).assetId).toBe('a')
    expect(wrapper.findAll('button').every(i => (i.element as HTMLButtonElement).disabled)).toBe(true)
  })
  it('announces busy state and binds a completed upload', async () => {
    let complete!: (value: unknown) => void
    vi.mocked(api).mockImplementation(() => new Promise(resolve => { complete = resolve }))
    const wrapper = setup()
    await upload(wrapper)
    expect(wrapper.emitted('busyChange')?.at(-1)).toEqual([true])
    expect(pickers(wrapper).every(p => p.props('disabled'))).toBe(true)
    complete(asset('done'))
    await flushPromises()
    expect((wrapper.props('mainImage') as Asset).assetId).toBe('done')
    expect(wrapper.emitted('busyChange')?.at(-1)).toEqual([false])
  })
  for (const change of [{ targetKey: 'product2' }, { canUpload: false }, { disabled: true }, { mainImage: asset('replacement') }]) {
    it(`ignores a late upload after ${Object.keys(change)[0]} changes`, async () => {
      let complete!: (value: unknown) => void
      vi.mocked(api).mockImplementation(() => new Promise(resolve => { complete = resolve }))
      const wrapper = setup()
      await upload(wrapper)
      await wrapper.setProps(change)
      complete(asset('late'))
      await flushPromises()
      expect(wrapper.emitted('update:mainImage')).toBeUndefined()
      expect(wrapper.emitted('busyChange')?.at(-1)).toEqual([false])
    })
  }
  it('does not emit a binding after unmount', async () => {
    let complete!: (value: unknown) => void
    vi.mocked(api).mockImplementation(() => new Promise(resolve => { complete = resolve }))
    const wrapper = setup()
    await upload(wrapper)
    wrapper.unmount()
    complete(asset('late'))
    await flushPromises()
    expect(wrapper.emitted('update:mainImage')).toBeUndefined()
  })
  it('does not upload after the target changes during asynchronous image validation', async () => {
    let complete!: (value: unknown) => void
    vi.stubGlobal('createImageBitmap', vi.fn(() => new Promise(resolve => { complete = resolve })))
    const wrapper = setup()
    await upload(wrapper)
    await wrapper.setProps({ targetKey: 'next' })
    complete({ width: 800, height: 800, close() {} })
    await flushPromises()
    expect(api).not.toHaveBeenCalled()
  })
  it('supports video upload and reports network errors without changing existing media', async () => {
    vi.mocked(api).mockResolvedValueOnce(asset('v1', 'VIDEO')).mockRejectedValueOnce(new Error('网络错误'))
    const wrapper = setup()
    await upload(wrapper, 'video')
    expect((wrapper.props('video') as Asset).assetId).toBe('v1')
    await upload(wrapper, 'video')
    expect(wrapper.text()).toContain('网络错误')
    expect((wrapper.props('video') as Asset).assetId).toBe('v1')
    expect(wrapper.emitted('busyChange')?.at(-1)).toEqual([false])
  })
})
