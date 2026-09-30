/** No HTTP mocks: isolated Django + PostgreSQL, actual CSRF/session and persistence. */
import { expect, test, type APIResponse, type Response } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { openSection, screenshot } from './fixtures'

test('real isolated database: one SPU row expands to independently priced SKUs', async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill(process.env.MALL_E2E_OWNER!)
  await page.getByLabel('密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'csrftoken')?.value
  expect(csrf).toBeTruthy()
  const headers = { 'X-CSRFToken': csrf!, Origin: new URL(page.url()).origin }
  const unique = `SPU_${testInfo.project.name}_${Date.now()}`
  async function created(response: APIResponse) {
    expect(response.status()).toBe(201)
    return (await response.json()).data
  }
  const parent = await created(await page.request.post('/api/v1/admin/categories', { headers,
    data: { parentId: null, name: `${unique} 一级`, status: 'ACTIVE', sortOrder: 1 } }))
  const leaf = await created(await page.request.post('/api/v1/admin/categories', { headers,
    data: { parentId: parent.id, name: `${unique} 二级`, status: 'ACTIVE', sortOrder: 1 } }))
  await created(await page.request.post('/api/v1/admin/products', { headers, data: {
    productNo: unique, name: `${unique} 商品`, categoryId: leaf.id, fulfillmentKind: 'SHIP',
    descriptionHtml: '', specAxes: [{ clientKey: 'pack', name: '包装', sortOrder: 0, options: [
      { clientKey: 'single', value: '单件', sortOrder: 0 },
      { clientKey: 'box', value: '整箱', sortOrder: 1 },
    ] }], skus: [
      { skuCode: `${unique}_S`, specOptionKeys: ['single'], listPriceFen: 100,
        saleStatus: 'OFF_SALE', gradePrices: [], unit: { baseUnit: '件', saleUnit: '件', ratio: 1 } },
      { skuCode: `${unique}_B`, specOptionKeys: ['box'], listPriceFen: 500,
        saleStatus: 'OFF_SALE', gradePrices: [], unit: { baseUnit: '件', saleUnit: '箱', ratio: 6 } },
    ],
  } }))
  await page.goto('/catalog')
  await page.getByPlaceholder('名称或编号').fill(unique)
  await page.getByRole('button', { name: '查询', exact: true }).click()
  const directory = page.getByLabel('商品列表，可横向滚动')
  await expect(directory.locator('tbody')).toHaveCount(1)
  await expect(directory).toContainText('¥1.00–¥5.00')
  await directory.getByRole('button', { name: '展开规格' }).click()
  await expect(directory.locator('.catalog-sku-card')).toHaveCount(2)
  await expect(directory).toContainText(`${unique}_S`)
  await expect(directory).toContainText(`${unique}_B`)
  await screenshot(page, testInfo, 'spu-directory-expanded')
  await page.getByRole('button', { name: 'SKU 批量管理' }).click()
  await expect(page.getByLabel('SKU 批量管理列表，可横向滚动').locator('tbody tr')).toHaveCount(2)
  await page.goto('/inventory')
  const poolPanel = page.getByRole('region', { name: '库存池绑定' })
  await expect(poolPanel).toBeVisible()
  await poolPanel.getByPlaceholder('输入商品名称或编码').fill(unique)
  await poolPanel.getByRole('button', { name: '查询商品' }).click()
  await poolPanel.getByRole('button', { name: new RegExp(`${unique} 商品`) }).click()
  await poolPanel.getByLabel('待绑定 SKU').selectOption({ label: `${unique}_B（件）` })
  await poolPanel.getByLabel('目标锚 SKU').selectOption({ label: `${unique}_S（件 · 待建池）` })
  await poolPanel.getByRole('button', { name: '核对并绑定' }).click()
  const post = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/admin/inventory/pool-bindings'
    && response.request().method() === 'POST')
  await page.getByRole('button', { name: '确认绑定' }).click()
  expect((await post).status()).toBe(200)
  await expect(poolPanel).toContainText(`与 ${unique}_S 共享`)
})

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
  await openSection(page, '小程序')
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

// Real local storage, decoder, database, CSRF and revision handling. No publication or platform call.
test('real isolated database: bulk upload images and persist a referenced product description', async ({ page }, testInfo) => {
  expect(process.env.MALL_E2E_OWNER, 'Run through the isolated integration launcher').toBeTruthy()
  expect(process.env.MALL_E2E_OWNER_PASSWORD).toBeTruthy()
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill(process.env.MALL_E2E_OWNER!)
  await page.getByLabel('密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  await page.goto('/assets')

  const unique = `${testInfo.project.name}_${Date.now()}`
  const filenames = [`description_${unique}_one.png`, `description_${unique}_two.png`]
  // The same committed synthetic PNG used by the isolated visual seed; both files must decode for real.
  const png = readFileSync(new URL('../../../mini-program/assets/startup-brand.png', import.meta.url))
  const responses: Response[] = []
  page.on('response', response => {
    if (new URL(response.url()).pathname === '/api/v1/admin/assets' && response.request().method() === 'POST') responses.push(response)
  })
  const upload = page.getByRole('region', { name: '批量上传素材', exact: true })
  await upload.locator('input[type=file]').setInputFiles(filenames.map(name => ({ name, mimeType: 'image/png', buffer: png })))
  await expect(upload.getByRole('list', { name: '上传队列' }).getByRole('listitem')).toHaveCount(2)
  await upload.getByRole('button', { name: '开始上传', exact: true }).click()
  await expect(upload.getByRole('status')).toContainText('成功 2 / 2 个', { timeout: 20_000 })
  expect(responses).toHaveLength(2)
  const receipts = await Promise.all(responses.map(async response => {
    expect(response.status()).toBe(201)
    return (await response.json()).data as { assetId: string; originalName: string }
  }))
  expect(new Set(receipts.map(row => row.assetId)).size).toBe(2)
  const chosen = receipts.find(row => row.originalName === filenames[0])!
  expect(chosen).toBeTruthy()

  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'csrftoken')?.value
  expect(csrf).toBeTruthy()
  const headers = { 'X-CSRFToken': csrf!, Origin: new URL(page.url()).origin }
  async function created(response: APIResponse) {
    // Diagnostics intentionally contain no login request or credential values.
    expect(response.status()).toBe(201)
    return (await response.json()).data
  }
  const parent = await created(await page.request.post('/api/v1/admin/categories', { headers,
    data: { parentId: null, name: `图文集成 ${unique}`, status: 'ACTIVE', sortOrder: 1 } }))
  const leaf = await created(await page.request.post('/api/v1/admin/categories', { headers,
    data: { parentId: parent.id, name: '描述图片', status: 'ACTIVE', sortOrder: 1 } }))
  const code = `DESC_${unique}`
  const product = await created(await page.request.post('/api/v1/admin/products', { headers, data: {
    productNo: code, name: `图文草稿 ${unique}`, categoryId: leaf.id, fulfillmentKind: 'SHIP',
    descriptionHtml: '', specAxes: [], skus: [{ skuCode: `${code}_SKU`, specOptionKeys: [], listPriceFen: 100,
      saleStatus: 'OFF_SALE', gradePrices: [], unit: { baseUnit: '件', saleUnit: '件', ratio: 1 } }],
  } }))
  const initialRead = await page.request.get(`/api/v1/admin/products/${product.productId}`)
  expect(initialRead.status()).toBe(200)
  expect((await initialRead.json()).data).toMatchObject({ status: 'DRAFT', productRevision: product.productRevision })
  await page.goto('/catalog')
  await page.getByPlaceholder('名称或编号').fill(code)
  await page.getByPlaceholder('名称或编号').press('Enter')
  await page.getByRole('row').filter({ hasText: code }).getByRole('button', { name: '编辑商品', exact: true }).click()
  const editor = page.getByRole('region', { name: '商品描述', exact: true })
  await editor.getByRole('textbox', { name: '商品描述', exact: true }).fill('真实隔离数据库图文验收')
  await editor.getByRole('textbox', { name: '商品描述', exact: true }).press('End')
  await editor.getByRole('button', { name: '从素材中心选择', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '选择素材', exact: true })
  await picker.locator('.asset-item').filter({ hasText: filenames[0] }).getByRole('button', { name: '选择素材', exact: true }).click()
  await editor.getByLabel('图片 1 说明', { exact: true }).fill('已持久化的描述素材')
  const savedEvent = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/admin/products/${product.productId}` && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: '保存商品资料', exact: true }).click()
  const saved = await savedEvent
  expect(saved.status()).toBe(200)
  expect(saved.request().postDataJSON().expectedRevision).toBe(product.productRevision)
  await expect(page.getByText('商品资料已保存', { exact: true })).toBeVisible()

  const reread = await page.request.get(`/api/v1/admin/products/${product.productId}`)
  expect(reread.status()).toBe(200)
  const persisted = (await reread.json()).data
  expect(persisted.productRevision).toBe(product.productRevision + 1)
  expect(persisted.descriptionHtml).toContain('真实隔离数据库图文验收')
  expect(persisted.descriptionHtml).toContain(`data-asset-id="${chosen.assetId}"`)
  expect(persisted.descriptionHtml).toContain(`src="/api/v1/admin/assets/${chosen.assetId}/file"`)
  const references = await page.request.get(`/api/v1/admin/assets/${chosen.assetId}/references?page=1&pageSize=20`)
  expect(references.status()).toBe(200)
  expect((await references.json()).data.items).toEqual(expect.arrayContaining([
    expect.objectContaining({ domain: 'CATALOG', objectId: product.productId, role: 'DESCRIPTION_IMAGE', state: 'BOUND' }),
  ]))
  await page.reload()
  await page.getByRole('row').filter({ hasText: code }).getByRole('button', { name: '编辑商品', exact: true }).click()
  await expect(editor.getByRole('textbox', { name: '商品描述', exact: true })).toContainText('真实隔离数据库图文验收')
  await expect(editor.getByLabel('图片 1 说明', { exact: true })).toHaveValue('已持久化的描述素材')
  await screenshot(page, testInfo, 'real-database-description-reference')
})
