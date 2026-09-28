import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import ProductCreateForm from '../../src/views/catalog/ProductCreateForm.vue'
import { api } from '../../src/api'

vi.mock('../../src/api', async importOriginal => ({ ...await importOriginal<typeof import('../../src/api')>(), api: vi.fn() }))
const image = { assetId: 'image1', kind: 'IMAGE', contentType: 'image/png', byteSize: 4, width: 800, height: 800, adminUrl: '/image1.png' }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.mocked(api).mockReset(); vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 800, height: 800, close() {} }))) })
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
function setup() {
  const account = ref({ accountId: 'a1', permissionCodes: ['catalog.write', 'asset.upload', 'asset.read'] })
  const wrapper = mount(ProductCreateForm, { props: { categories: [] }, global: { provide: { 'admin-account': account }, stubs: { AssetPicker: true } } })
  wrappers.push(wrapper)
  return { wrapper, account }
}
async function upload(wrapper: ReturnType<typeof mount>, slot = 0, file = new File(['data'], 'image.png', { type: 'image/png' })) {
  const input = wrapper.findAll('input[type="file"]')[slot]!
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

describe('product create media behavior', () => {
  it('uploads the main image and marks the parent form dirty', async () => {
    vi.mocked(api).mockResolvedValue(image)
    const { wrapper } = setup()
    await upload(wrapper)
    expect(wrapper.get('img[alt="当前商品主图"]').attributes('src')).toBe('/image1.png')
    expect(wrapper.emitted('dirtyChange')?.at(-1)).toEqual([true])
  })
  it('reports an upload failure and allows retry without losing the form', async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error('上传失败')).mockResolvedValue(image)
    const { wrapper } = setup()
    await wrapper.get('input[placeholder="请输入商品名称"]').setValue('保留名称')
    await upload(wrapper)
    expect(wrapper.text()).toContain('上传失败')
    await upload(wrapper)
    expect(wrapper.find('img').exists()).toBe(true)
    expect((wrapper.get('input[placeholder="请输入商品名称"]').element as HTMLInputElement).value).toBe('保留名称')
  })
  it('does not bind an upload after upload permission is revoked', async () => {
    let complete!: (value: unknown) => void
    vi.mocked(api).mockImplementation(() => new Promise(resolve => { complete = resolve }))
    const { wrapper, account } = setup()
    await upload(wrapper)
    account.value = { ...account.value, permissionCodes: ['catalog.write', 'asset.read'] }
    await flushPromises()
    complete(image)
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
  })
  it('keeps the parent busy and blocks cancelling or saving until upload completes', async () => {
    let complete!: (value: unknown) => void
    vi.mocked(api).mockImplementation(() => new Promise(resolve => { complete = resolve }))
    const { wrapper } = setup()
    await upload(wrapper)
    expect(wrapper.emitted('busyChange')?.at(-1)).toEqual([true])
    expect((wrapper.findAll('button').find(b => b.text() === '取消')!.element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('button[type="submit"]').element as HTMLButtonElement).disabled).toBe(true)
    complete(image)
    await flushPromises()
    expect(wrapper.emitted('busyChange')?.at(-1)).toEqual([false])
    expect((wrapper.findAll('button').find(b => b.text() === '取消')!.element as HTMLButtonElement).disabled).toBe(false)
  })
  it('uses the existing file validation before requesting an upload', async () => {
    const { wrapper } = setup()
    await upload(wrapper, 0, new File(['bad'], 'file.txt', { type: 'text/plain' }))
    expect(wrapper.text()).toContain('图片仅支持 JPG 或 PNG 文件')
    expect(api).not.toHaveBeenCalled()
  })
})
