import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PasswordDialog from '../../src/components/PasswordDialog.vue'
import AccountsView from '../../src/views/AccountsView.vue'
import { api, ApiError, type Account } from '../../src/api'

vi.mock('../../src/api', async original => ({ ...await original<typeof import('../../src/api')>(), api: vi.fn() }))
const target: Account = { accountId: 'staff-id', loginName: 'staff', displayName: '运营同事', kind: 'STAFF', enabled: true, revision: 4, groupIds: [], permissionCodes: [] }
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => { vi.mocked(api).mockReset() })
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); document.body.innerHTML = '' })
async function setup(resetTarget?: Account) {
  const wrapper = mount(PasswordDialog, { props: { target: resetTarget }, attachTo: document.body, global: { stubs: { teleport: true } } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
async function fill(wrapper: ReturnType<typeof mount>, repeat = 'New safe password 2027!') {
  await wrapper.get('input[autocomplete="current-password"]').setValue('Old safe password 2026!')
  const inputs = wrapper.findAll('input[autocomplete="new-password"]')
  await inputs[0]!.setValue('New safe password 2027!')
  await inputs[1]!.setValue(repeat)
}

describe('admin password changes', () => {
  it('accepts six characters and rejects five before submitting', async () => {
    const wrapper = await setup()
    expect(wrapper.text()).toContain('至少 6 个字符')
    await fill(wrapper)
    const inputs = wrapper.findAll('input[autocomplete="new-password"]')
    for (const input of inputs) await input.setValue('12345')
    await wrapper.get('form').trigger('submit')
    expect(api).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('至少 6 个字符')
    for (const input of inputs) await input.setValue('123456')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(api).toHaveBeenCalledTimes(1)
  })
  it('requires all fields and clears entered secrets when cancelled', async () => {
    const wrapper = await setup()
    await wrapper.get('form').trigger('submit')
    expect(wrapper.text()).toContain('请填写当前密码、新密码和确认新密码')
    expect(api).not.toHaveBeenCalled()
    await fill(wrapper)
    await wrapper.findAll('button').find(button => button.text() === '取消')!.trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(wrapper.findAll('input').every(input => (input.element as HTMLInputElement).value === '')).toBe(true)
  })
  it('checks password confirmation before sending and clears all secrets after success', async () => {
    const wrapper = await setup()
    await fill(wrapper, 'mismatch')
    await wrapper.get('form').trigger('submit')
    expect(api).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('两次输入的新密码不一致')
    await wrapper.findAll('input[autocomplete="new-password"]')[1]!.setValue('New safe password 2027!')
    vi.mocked(api).mockResolvedValue({ requiresLogin: true })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api).toHaveBeenCalledWith('/auth/password', { method: 'POST', body: JSON.stringify({ currentPassword: 'Old safe password 2026!', newPassword: 'New safe password 2027!' }) })
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(wrapper.findAll('input').every(input => (input.element as HTMLInputElement).value === '')).toBe(true)
  })
  it('binds owner reset to target revision and prevents duplicate submissions', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(api).mockReturnValue(new Promise(resolve => { finish = resolve }))
    const wrapper = await setup(target)
    expect(wrapper.text()).toContain('重置 运营同事 的密码')
    await fill(wrapper)
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    expect(api).toHaveBeenCalledTimes(1)
    expect(api).toHaveBeenCalledWith('/accounts/staff-id/password', expect.objectContaining({ body: JSON.stringify({ currentPassword: 'Old safe password 2026!', newPassword: 'New safe password 2027!', expectedRevision: 4 }) }))
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    finish({ requiresLogin: false })
    await flushPromises()
  })
  it('shows server errors and clears passwords for retry', async () => {
    vi.mocked(api).mockImplementation(async () => { throw new ApiError('当前密码不正确。', 403, 'CONFIRMATION_FAILED') })
    const wrapper = await setup()
    await fill(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('当前密码不正确')
    expect(wrapper.findAll('input').every(input => (input.element as HTMLInputElement).value === '')).toBe(true)
    expect(wrapper.emitted('changed')).toBeUndefined()
  })
  it('requests a fresh account list on a revision conflict', async () => {
    vi.mocked(api).mockImplementation(async () => { throw new ApiError('账号已被修改', 409, 'REVISION_CONFLICT') })
    const wrapper = await setup(target)
    await fill(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.emitted('stale')).toHaveLength(1)
  })
  it('only displays staff reset actions to the owner, even when staff can manage accounts', async () => {
    vi.mocked(api).mockResolvedValue([target])
    const wrapper = mount(AccountsView, { props: { account: { ...target, kind: 'OWNER', accountId: 'owner', permissionCodes: ['account.read', 'account.manage'] } } })
    wrappers.push(wrapper)
    await flushPromises()
    expect(wrapper.text()).toContain('重置密码')
    await wrapper.setProps({ account: { ...target, permissionCodes: ['account.read', 'account.manage', 'account.reset_credentials'] } })
    expect(wrapper.text()).not.toContain('重置密码')
  })
})
