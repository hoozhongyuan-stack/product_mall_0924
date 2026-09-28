import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { usePageTargets } from '../../src/views/pages/targets'

const response = (data: unknown, status = 200) => new Response(JSON.stringify({ success: true, data }), { status })
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
function setup() {
  let loader!: ReturnType<typeof usePageTargets>
  const wrapper = mount(defineComponent({ setup() { loader = usePageTargets(); return () => null } }))
  wrappers.push(wrapper)
  return { loader, wrapper }
}
function fetcher(category: () => Promise<Response> | Response, page = () => response({ rows: [], total: 0 })) {
  vi.stubGlobal('fetch', vi.fn((path: string) => {
    if (path.includes('/categories')) return category()
    if (path.includes('/products')) return response({ rows: [] })
    return page()
  }))
}

describe('page targets concurrent loading', () => {
  it('only the latest request can publish choices and loading state', async () => {
    let resolveOld!: (value: Response) => void
    const old = new Promise<Response>(resolve => { resolveOld = resolve })
    let calls = 0
    fetcher(() => ++calls === 1 ? old : response([{ id: 'new', name: 'New' }]))
    const { loader } = setup()
    const pending = loader.load()
    expect(loader.loading.value).toBe(true)
    await loader.load()
    expect(loader.categories.value[0]?.id).toBe('new')
    resolveOld(response([{ id: 'old', name: 'Old' }]))
    await pending
    expect(loader.categories.value[0]?.id).toBe('new')
    expect(loader.loading.value).toBe(false)
  })
  it('discards a response after the editor unmounts', async () => {
    let resolve!: (value: Response) => void
    fetcher(() => new Promise<Response>(done => { resolve = done }))
    const { loader, wrapper } = setup()
    const pending = loader.load()
    wrapper.unmount()
    resolve(response([{ id: 'old', name: 'Old' }]))
    await pending
    expect(loader.categories.value).toEqual([])
  })
  it('preserves successful categories when the admin target request fails', async () => {
    fetcher(() => response([{ id: 'valid', name: 'Valid' }]), () => response({ rows: [], total: 0 }, 500))
    const { loader } = setup()
    await loader.load()
    expect(loader.categories.value[0]?.id).toBe('valid')
    expect(loader.pages.value).toEqual([])
    expect(loader.warning.value).toContain('部分目标列表暂不可用')
  })
  it('rejects malformed target rows instead of showing unverified links', async () => {
    fetcher(() => response([{ name: 'No ID' }]), () => response({ rows: [{ pageId: 'p1', name: 'missing revision' }], total: 1 }))
    const { loader } = setup()
    await loader.load()
    expect(loader.categories.value).toEqual([])
    expect(loader.pages.value).toEqual([])
    expect(loader.warning.value).toContain('部分目标列表暂不可用')
  })
  it('shows a truncation notice for a valid partial list', async () => {
    fetcher(() => response([]), () => response({ rows: [], total: 51 }))
    const { loader } = setup()
    await loader.load()
    await flushPromises()
    expect(loader.warning.value).toContain('只显示前 50 项')
    expect(loader.loading.value).toBe(false)
  })
})
