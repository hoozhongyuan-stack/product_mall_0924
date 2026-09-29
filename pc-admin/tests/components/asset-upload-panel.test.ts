import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import AssetUploadQueue from '../../src/shared/AssetUploadQueue.vue'
import { ApiError, api } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.mocked(api).mockReset() })
function render(kind?: 'IMAGE' | 'VIDEO' | 'GIF') {
  const account = ref({ accountId: 'one', permissionCodes: ['asset.upload'] })
  const wrapper = mount(AssetUploadQueue, { props: { canUpload: true, kind }, global: {
    provide: { 'admin-account': account },
    stubs: { 'el-upload': { name: 'TestUpload', props: ['onChange', 'onExceed', 'accept'], template: '<div><button @click="onChange({ raw: newFile })">选文件</button><slot /></div>', data: () => ({ newFile: new File(['x'], 'one.png', { type: 'image/png' }) }) } },
  } })
  wrappers.push(wrapper)
  return { wrapper, account }
}
it('shows selected files before upload, uses the shared API and refreshes once', async () => {
  vi.mocked(api).mockResolvedValue({ assetId: 'one' } as never)
  const { wrapper } = render()
  await wrapper.findAll('button').find(button => button.text() === '选文件')!.trigger('click')
  expect(wrapper.text()).toContain('one.png')
  expect(api).not.toHaveBeenCalled()
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(true)
  await wrapper.findAll('button').find(button => button.text() === '开始上传')!.trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledWith('/assets', expect.objectContaining({ method: 'POST', body: expect.any(FormData) }))
  expect(wrapper.text()).toContain('上传成功')
  expect(wrapper.emitted('refresh')).toHaveLength(1)
  const finished = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(finished)
  expect(finished.defaultPrevented).toBe(false)
})

it('offers an explicit retry only for a rejected file and clears successful receipts', async () => {
  vi.mocked(api).mockRejectedValueOnce(new ApiError('文件无效', 400, 'INVALID_FILE')).mockResolvedValue({ assetId: 'saved' } as never)
  const { wrapper } = render()
  await wrapper.findAll('button').find(b => b.text() === '选文件')!.trigger('click')
  await wrapper.findAll('button').find(b => b.text() === '开始上传')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('文件无效')
  await wrapper.get('button[aria-label="重试 one.png"]').trigger('click')
  await flushPromises()
  expect(api).toHaveBeenCalledTimes(2)
  expect(wrapper.text()).toContain('上传成功')
  await wrapper.findAll('button').find(b => b.text() === '清除成功记录')!.trigger('click')
  expect(wrapper.find('ul').exists()).toBe(false)
})

it('provides list verification instead of retry for an uncertain response', async () => {
  vi.mocked(api).mockRejectedValue(new TypeError('offline'))
  const { wrapper } = render()
  await wrapper.findAll('button').find(b => b.text() === '选文件')!.trigger('click')
  await wrapper.findAll('button').find(b => b.text() === '开始上传')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('结果待确认')
  expect(wrapper.find('button[aria-label="重试 one.png"]').exists()).toBe(false)
  await wrapper.findAll('button').find(b => b.text() === '核对素材列表')!.trigger('click')
  expect(wrapper.emitted('refresh')).toHaveLength(2)
  await wrapper.get('button[aria-label="移出队列 one.png"]').trigger('click')
  expect(wrapper.find('ul').exists()).toBe(false)
  expect(api).toHaveBeenCalledTimes(1)
})

it('stops subsequent files while displaying the actual in-flight result', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementationOnce(() => new Promise(yes => { resolve = yes }) as never)
  const { wrapper } = render()
  const select = wrapper.findAll('button').find(b => b.text() === '选文件')!
  await select.trigger('click'); await select.trigger('click')
  await wrapper.findAll('button').find(b => b.text() === '开始上传')!.trigger('click')
  await wrapper.findAll('button').find(b => b.text() === '停止后续上传')!.trigger('click')
  expect(wrapper.text()).toContain('当前请求结束后停止')
  resolve({ assetId: 'one' })
  await flushPromises()
  expect(wrapper.text()).toContain('继续上传')
  expect(wrapper.text()).toContain('等待上传')
  expect(api).toHaveBeenCalledTimes(1)
})

it.each([['VIDEO', 'video/mp4', '50 MiB'], ['GIF', 'image/gif', '10 MiB']] as const)(
  'keeps constrained %s pickers restricted and shows invalid file/limit feedback', async (kind, accept, size) => {
    const { wrapper } = render(kind)
    const upload = wrapper.findComponent({ name: 'TestUpload' })
    expect(upload.props('accept')).toBe(accept)
    expect(wrapper.text()).toContain(size)
    expect(wrapper.find('label').exists()).toBe(false)
    upload.props('onChange')({})
    await wrapper.findAll('button').find(b => b.text() === '选文件')!.trigger('click')
    expect(wrapper.text()).toContain('校验未通过')
    expect(wrapper.text()).toContain('文件类型不匹配')
    upload.props('onExceed')()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('20')
    await wrapper.setProps({ canUpload: false })
    expect(wrapper.text()).not.toContain('one.png')
  },
)
it('clears the previous account queue and rejects a stale in-flight success', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(api).mockImplementation(() => new Promise(yes => { resolve = yes }) as never)
  const { wrapper, account } = render()
  await wrapper.findAll('button').find(button => button.text() === '选文件')!.trigger('click')
  await wrapper.findAll('button').find(button => button.text() === '开始上传')!.trigger('click')
  account.value = { accountId: 'two', permissionCodes: ['asset.upload'] }
  await flushPromises()
  resolve({ assetId: 'old' })
  await flushPromises()
  expect(wrapper.text()).not.toContain('one.png')
  expect(wrapper.emitted('refresh')).toBeUndefined()
})
