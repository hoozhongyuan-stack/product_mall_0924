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

// Synthetic credentials only. This test never invokes the live WeChat probe.
test('real isolated database: securely replace and retain mini-program credentials', async ({ page }, testInfo) => {
  const password = process.env.MALL_E2E_OWNER_PASSWORD!
  const syntheticSecret = `synthetic-only-${testInfo.project.name}-${Date.now()}`
  const appId = testInfo.project.name === 'desktop' ? 'wx1111111111111111' : 'wx2222222222222222'
  const endpoint = '/api/v1/admin/integrations/wechat-mini-program'
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill(process.env.MALL_E2E_OWNER!)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await openSection(page, '店铺与小程序')
  await page.getByRole('link', { name: '小程序接入', exact: true }).click()
  await page.getByLabel('小程序 AppID', { exact: true }).fill(appId)
  await page.getByText('替换密钥', { exact: true }).click()
  await expect(page.getByRole('radio', { name: '替换密钥', exact: true })).toBeChecked()
  await page.getByLabel('新的 AppSecret', { exact: true }).fill(syntheticSecret)
  await page.getByRole('button', { name: '保存配置', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill(password)
  const savedEvent = page.waitForResponse(response => response.url().endsWith(endpoint)
    && response.request().method() === 'PUT')
  await page.getByRole('button', { name: '确认保存', exact: true }).click()
  const saved = await savedEvent
  expect(saved.status()).toBe(200)
  const receipt = await saved.json()
  expect(receipt.data).toMatchObject({ source: 'MANAGED', appId, secretConfigured: true })
  expect(JSON.stringify(receipt)).not.toContain(syntheticSecret)
  await expect(page.getByLabel('新的 AppSecret', { exact: true })).toBeHidden()
  await expect(page.getByLabel('当前账号密码', { exact: true })).toBeHidden()
  const readEvent = page.waitForResponse(response => response.url().endsWith(endpoint)
    && response.request().method() === 'GET')
  await page.reload()
  const reread = await (await readEvent).json()
  expect(reread.data).toMatchObject({ source: 'MANAGED', appId, secretConfigured: true, revision: receipt.data.revision })
  expect(JSON.stringify(reread)).not.toContain(syntheticSecret)
  await expect(page.getByLabel('小程序 AppID', { exact: true })).toHaveValue(appId)
  await expect(page.getByRole('radio', { name: '保留已配置密钥', exact: true })).toBeChecked()
  await page.getByRole('button', { name: '保存配置', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill(password)
  const keptEvent = page.waitForResponse(response => response.url().endsWith(endpoint)
    && response.request().method() === 'PUT')
  await page.getByRole('button', { name: '确认保存', exact: true }).click()
  const kept = await keptEvent
  expect(kept.status()).toBe(200)
  expect(kept.request().postDataJSON()).toEqual({
    expectedRevision: reread.data.revision, appId, secretAction: 'KEEP',
  })
  expect((await kept.json()).data).toMatchObject({ revision: reread.data.revision + 1, secretConfigured: true })
  await expect(page.getByLabel('当前账号密码', { exact: true })).toBeHidden()
  const browserState = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, html: document.body.innerHTML }))
  expect(browserState).not.toContain(syntheticSecret)
  expect(browserState).not.toContain(password)
  await screenshot(page, testInfo, 'real-database-wechat-secret-redacted')
})
