import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, onBeforeUnmount, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AssetsView from '../../src/views/AssetsView.vue'
import AssetPicker from '../../src/shared/AssetPicker.vue'
import { confirmLeavingUploads } from '../../src/shared/upload-navigation'
import { createAssetUploadQueue } from '../../src/shared/asset-upload-queue'

it('requires confirmation before leaving an asset upload and keeps the page when canceled', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/assets', component: AssetsView, props: { account: { permissionCodes: ['asset.upload'] } } },
    { path: '/other', component: { template: '<p>other</p>' } },
  ] })
  await router.push('/assets')
  const wrapper = mount(RouterView, { global: { plugins: [router], stubs: { AssetBrowser: defineComponent({ emits: ['pending'], template: '<button @click="$emit(\'pending\', true)">排队</button>' }) } } })
  try {
    await wrapper.get('button').trigger('click')
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValueOnce('cancel')
    await router.push('/other')
    expect(router.currentRoute.value.path).toBe('/assets')
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValueOnce('confirm')
    await router.push('/other')
    expect(router.currentRoute.value.path).toBe('/other')
  } finally { wrapper.unmount() }
})

it('protects the asset picker cancel action while files are queued', async () => {
  const wrapper = mount(AssetPicker, { props: { kind: 'IMAGE', targetKey: 'product:one' }, global: {
    provide: { 'admin-account': { value: { permissionCodes: ['asset.upload'] } } },
    stubs: {
      'el-button': { template: '<button><slot /></button>' },
      'el-dialog': { props: ['modelValue'], template: '<section v-if="modelValue"><slot /><slot name="footer" /></section>' },
      AssetBrowser: defineComponent({ emits: ['pending'], template: '<button @click="$emit(\'pending\', true)">排队</button>' }),
    },
  } })
  try {
    await wrapper.findAll('button').find(b => b.text() === '从素材中心选择')!.trigger('click')
    await wrapper.findAll('button').find(b => b.text() === '排队')!.trigger('click')
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValueOnce('cancel')
    await wrapper.findAll('button').find(b => b.text() === '取消选择')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('section').exists()).toBe(true)
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValueOnce('confirm')
    await wrapper.findAll('button').find(b => b.text() === '取消选择')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('section').exists()).toBe(false)
  } finally { wrapper.unmount() }
})

it('uses explicit wording that an in-flight write may finish remotely', async () => {
  const confirm = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValueOnce('confirm')
  expect(await confirmLeavingUploads()).toBe(true)
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('当前请求可能仍在服务器完成'), expect.any(String), expect.any(Object))
})

it('protects browser back from a nested picker even when its parent form is pristine', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/other', component: { template: '<p>other</p>' } },
    { path: '/editor', component: AssetPicker, props: { kind: 'IMAGE', targetKey: 'product:one' } },
  ] })
  await router.push('/other'); await router.push('/editor')
  const wrapper = mount(RouterView, { global: {
    plugins: [router], provide: { 'admin-account': { value: { permissionCodes: ['asset.upload'] } } },
    stubs: {
      'el-button': { template: '<button><slot /></button>' },
      'el-dialog': { props: ['modelValue'], template: '<section v-if="modelValue"><slot /><slot name="footer" /></section>' },
      AssetBrowser: defineComponent({ emits: ['pending'], template: '<button @click="$emit(\'pending\', true)">排队</button>' }),
    },
  } })
  try {
    await wrapper.findAll('button').find(b => b.text() === '从素材中心选择')!.trigger('click')
    await wrapper.findAll('button').find(b => b.text() === '排队')!.trigger('click')
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValueOnce('cancel')
    router.back(); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/editor')
    expect(wrapper.text()).toContain('排队')
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValueOnce('confirm')
    router.back(); await flushPromises()
    expect(router.currentRoute.value.path).toBe('/other')
  } finally { wrapper.unmount() }
})

it('stops queued dispatch only after a reused route update commits, retaining uploads on cancelled navigation', async () => {
  const asset = { assetId: 'first', kind: 'IMAGE' as const, contentType: 'image/png', byteSize: 1, adminUrl: '/file' }
  let finishFirst!: (value: typeof asset) => void
  const upload = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve })).mockResolvedValue(asset)
  const browser = defineComponent({
    emits: ['pending'],
    setup(_, { emit }) {
      const queue = createAssetUploadQueue(upload, () => true)
      watch(queue.hasPending, value => emit('pending', value), { immediate: true })
      onBeforeUnmount(() => { queue.invalidate(); emit('pending', false) })
      function enqueue() {
        queue.add(['first.png', 'second.png'].map(name => new File(['x'], name, { type: 'image/png' })), 'IMAGE')
        void queue.start()
      }
      return { enqueue }
    },
    template: '<button @click="enqueue">开始批量</button>',
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/editor/:id', component: AssetPicker, props: { kind: 'IMAGE', targetKey: 'product' } },
  ] })
  await router.push('/editor/one')
  const wrapper = mount(RouterView, { global: {
    plugins: [router], provide: { 'admin-account': { value: { permissionCodes: ['asset.upload'] } } },
    stubs: {
      'el-button': { template: '<button><slot /></button>' },
      'el-dialog': { props: ['modelValue'], template: '<section v-if="modelValue"><slot /><slot name="footer" /></section>' },
      AssetBrowser: browser,
    },
  } })
  let allowCommit = true
  router.beforeResolve(() => allowCommit)
  try {
    await wrapper.findAll('button').find(button => button.text() === '从素材中心选择')!.trigger('click')
    await wrapper.findAll('button').find(button => button.text() === '开始批量')!.trigger('click')
    expect(upload).toHaveBeenCalledTimes(1)
    const confirm = vi.spyOn(ElMessageBox, 'confirm')
    confirm.mockRejectedValueOnce('cancel')
    await router.push('/editor/two')
    expect(router.currentRoute.value.path).toBe('/editor/one')
    expect(wrapper.find('section').exists()).toBe(true)
    allowCommit = false
    confirm.mockResolvedValueOnce('confirm')
    await router.push('/editor/two')
    expect(router.currentRoute.value.path).toBe('/editor/one')
    expect(wrapper.find('section').exists()).toBe(true)
    allowCommit = true
    confirm.mockResolvedValueOnce('confirm')
    await router.push('/editor/two')
    await flushPromises()
    finishFirst(asset)
    await flushPromises()
    expect(upload).toHaveBeenCalledTimes(1)
    expect(wrapper.find('section').exists()).toBe(false)
    expect(router.currentRoute.value.path).toBe('/editor/two')
  } finally { wrapper.unmount() }
})
