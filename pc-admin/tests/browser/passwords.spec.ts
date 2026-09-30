import { expect, test } from '@playwright/test'
import { account, installMockApi, ok, screenshot } from './fixtures'

test('mock HTTP: admins change their own password and return to login', async ({ page }, testInfo) => {
  let requestBody: unknown
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/auth/password') { requestBody = route.request().postDataJSON(); await ok(route, { requiresLogin: true }); return true }
    return false
  })
  await page.goto('/')
  await page.getByRole('button', { name: '修改密码', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '修改我的密码' })
  await expect(dialog).toBeVisible()
  await expect(page.locator('.dialog-fade-enter-active')).toHaveCount(0)
  await screenshot(page, testInfo, 'self-password-dialog')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await dialog.getByLabel('当前密码', { exact: true }).fill('Synthetic old password 2026!')
  await dialog.getByLabel('新密码', { exact: true }).fill('Synthetic replacement 2027!')
  await dialog.getByLabel('确认新密码', { exact: true }).fill('Synthetic replacement 2027!')
  await dialog.getByRole('button', { name: '保存新密码' }).click()
  await expect(page.getByRole('heading', { name: '欢迎登录' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('密码已修改，请使用新密码重新登录')
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  expect(requestBody).toEqual({ currentPassword: 'Synthetic old password 2026!', newPassword: 'Synthetic replacement 2027!' })
  verify()
})

test('mock HTTP: owner resets staff password with current owner password', async ({ page }, testInfo) => {
  const staff = { ...account, accountId: 'staff-password-test', loginName: 'shop-operator', displayName: '商品运营', kind: 'STAFF', revision: 7 }
  let requestBody: unknown
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/me') { await ok(route, { ...account, permissionCodes: ['account.read', 'account.manage'] }); return true }
    if (path === '/api/v1/admin/accounts') { await ok(route, [account, staff]); return true }
    if (path === `/api/v1/admin/accounts/${staff.accountId}/password`) { requestBody = route.request().postDataJSON(); await ok(route, { requiresLogin: false }); return true }
    return false
  })
  await page.goto('/accounts')
  await page.getByRole('button', { name: '重置密码', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '重置 商品运营 的密码' })
  await expect(dialog).toBeVisible()
  await expect(page.locator('.dialog-fade-enter-active')).toHaveCount(0)
  await screenshot(page, testInfo, 'staff-password-reset-dialog')
  await dialog.getByLabel('主账号当前密码', { exact: true }).fill('Synthetic owner password 2026!')
  await dialog.getByLabel('新密码', { exact: true }).fill('Synthetic staff password 2027!')
  await dialog.getByLabel('确认新密码', { exact: true }).fill('Synthetic staff password 2027!')
  await dialog.getByRole('button', { name: '确认重置' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  expect(requestBody).toEqual({ currentPassword: 'Synthetic owner password 2026!', newPassword: 'Synthetic staff password 2027!', expectedRevision: 7 })
  verify()
})
