/** No HTTP mocks: isolated Django + PostgreSQL, actual CSRF/session and persistence. */
import { expect, test } from '@playwright/test'
import { openSection, screenshot } from './fixtures'

test('real isolated database: login and create a persisted coupon draft', async ({ page }, testInfo) => {
  const loginName = process.env.MALL_E2E_OWNER
  const password = process.env.MALL_E2E_OWNER_PASSWORD
  expect(loginName, 'Use scripts/run-browser-integration.py to create isolated credentials').toBeTruthy()
  expect(password).toBeTruthy()
  await page.goto('/')
  await expect(page.getByLabel('账号', { exact: true })).toBeVisible()
  await page.getByLabel('账号', { exact: true }).fill(loginName!)
  await page.getByLabel('密码', { exact: true }).fill(password!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await openSection(page, '营销')
  await expect(page.getByRole('navigation', { name: '营销功能' })).toBeVisible()
  await page.getByRole('link', { name: /创建活动/ }).click()
  const code = `E2E_${testInfo.project.name}_${Date.now()}`
  const title = `隔离验收草稿 ${testInfo.project.name}`
  await page.getByLabel('活动编码', { exact: true }).fill(code)
  await page.getByLabel('活动名称', { exact: true }).fill(title)
  await page.getByLabel('开始时间', { exact: true }).fill(new Date(Date.now() + 86_400_000).toISOString().slice(0, 16))
  await page.getByLabel('截止时间', { exact: true }).fill(new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 16))
  const responseEvent = page.waitForResponse(response => response.url().endsWith('/api/v1/admin/coupon-campaigns')
    && response.request().method() === 'POST')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  const response = await responseEvent
  expect(response.status()).toBe(201)
  const payload = await response.json()
  expect(payload.data.status).toBe('DRAFT')
  expect(payload.data.code).toBe(code)
  await expect(page).toHaveURL(new RegExp(`/coupons/${payload.data.id}$`))
  await page.reload()
  await expect(page.getByLabel('活动编码', { exact: true })).toHaveValue(code)
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue(title)
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  await screenshot(page, testInfo, 'real-database-persisted-draft')
})
