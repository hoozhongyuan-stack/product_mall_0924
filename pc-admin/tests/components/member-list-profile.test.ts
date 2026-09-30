import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MembersView from '../../src/views/MembersView.vue'
import type { Account } from '../../src/api'
vi.mock('../../src/api', () => ({ api: vi.fn() }))
import { api } from '../../src/api'
const account = { accountId: 'a1', permissionCodes: ['member.read'] } as Account
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.clearAllMocks() })
describe('member profile list', () => {
  it('shows member number, avatar, nickname and phone while retaining internal detail links', async () => {
    vi.mocked(api).mockResolvedValue({ grades: [], pagination: { total: 1, page: 1, pageSize: 20 }, items: [{ id: 'internal-id', memberNo: 'm20260930130512A7x', nickname: '小明', avatarUrl: '/api/v1/app/member-avatars/avatar/file', phone: '13800138000', grade: { name: '普通会员' }, enabled: true, effectiveSpendFen: 0, points: { availablePoints: 0, frozenPoints: 0, debtPoints: 0 } }] })
    const w = mount(MembersView, { props: { account }, global: { stubs: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } } } }); wrappers.push(w); await flushPromises()
    expect(w.text()).toContain('m20260930130512A7x'); expect(w.text()).toContain('小明'); expect(w.text()).toContain('13800138000'); expect(w.find('img').attributes('src')).toContain('member-avatars'); expect(w.find('a').attributes('href')).toBe('/members/internal-id')
    await w.find('img').trigger('error'); expect(w.find('img').exists()).toBe(false); expect(w.find('[aria-label="未设置头像"]').exists()).toBe(true)
    await w.find('form').trigger('submit'); await flushPromises(); expect(w.find('img').exists()).toBe(true)
  })
  it('shows placeholders and queries the shared search field', async () => {
    vi.mocked(api).mockResolvedValue({ grades: [], pagination: { total: 1, page: 1, pageSize: 20 }, items: [{ id: 'legacy', memberNo: 'm20260930130512B8y', grade: { name: '普通会员' }, enabled: true, effectiveSpendFen: 0, points: { availablePoints: 0, frozenPoints: 0, debtPoints: 0 } }] })
    const w = mount(MembersView, { props: { account }, global: { stubs: ['RouterLink'] } }); wrappers.push(w); await flushPromises(); expect(w.text()).toContain('未设置昵称'); expect(w.text()).toContain('未绑定'); expect(w.find('img').exists()).toBe(false)
    await w.find('input').setValue('13800138000'); await w.find('form').trigger('submit'); await flushPromises(); expect(vi.mocked(api).mock.lastCall?.[0]).toContain('search=13800138000')
  })
})
