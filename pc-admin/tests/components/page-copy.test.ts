import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PageCopyPanel from '../../src/views/pages/PageCopyPanel.vue'

afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals() })
describe('page copy recovery', () => {
  it('retains request identity on a lost response and retries the same frozen body', async () => {
    const requests: { url: string; options: RequestInit }[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (url.endsWith('/auth/csrf')) return new Response(JSON.stringify({ success: true, data: { csrfToken: 'test-csrf' } }))
      requests.push({ url, options })
      if (requests.length === 1) throw new Error('连接中断')
      return new Response(JSON.stringify({ success: true, data: { pageId: 'copied', name: '新页面', revision: 1, publishedRevision: null } }))
    }))
    const wrapper = mount(PageCopyPanel, { props: { accountId: 'a', pageId: 'p1', name: '活动', revision: 3, publishedRevision: 2, disabled: false } })
    await wrapper.get('button').trigger('click')
    await wrapper.get('input').setValue('新页面')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('恢复原复制请求')
    await wrapper.findAll('button').find(b => b.text() === '恢复原复制请求')!.trigger('click'); await flushPromises()
    expect(requests).toHaveLength(2)
    expect(requests[0].options.body).toEqual(requests[1].options.body)
    expect(requests[0].options.headers).toEqual(requests[1].options.headers)
    expect(wrapper.emitted('copied')?.[0]?.[0]).toMatchObject({ pageId: 'copied' })
    wrapper.unmount()
  })
  it('restores a pending copy across mounts and never replaces its origin', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/auth/csrf')) return new Response(JSON.stringify({ success: true, data: { csrfToken: 'test-csrf' } }))
      throw new Error('连接中断')
    }))
    const props = { accountId: 'a', pageId: 'p1', name: '活动', revision: 3, publishedRevision: null }
    const first = mount(PageCopyPanel, { props })
    await first.get('button').trigger('click')
    await first.get('form').trigger('submit'); await flushPromises()
    first.unmount()
    const second = mount(PageCopyPanel, { props: { ...props, pageId: 'p2', revision: 8 } })
    expect(second.text()).toContain('恢复原复制请求')
    await second.findAll('button').find(b => b.text() === '恢复原复制请求')!.trigger('click'); await flushPromises()
    const calls = vi.mocked(fetch).mock.calls.filter(call => String(call[0]).includes('/copy'))
    expect(String(calls[1][0])).toContain('/pages/p1/copy')
    expect(JSON.parse(String(calls[1][1]?.body)).expectedRevision).toBe(3)
    second.unmount()
  })
})
