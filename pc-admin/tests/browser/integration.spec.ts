/** No HTTP mocks: isolated Django + PostgreSQL, actual CSRF/session and persistence. */
import { expect, test, type APIResponse, type Response } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { openSection, screenshot } from './fixtures'

test('real isolated database: unit specifications automatically use one stock identity and persist after reload', async ({ page }, testInfo) => {
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
    descriptionHtml: '', unitConversion: { axisKey: 'pack', baseOptionKey: 'single', ratios: [{ optionKey: 'single', ratio: 1 }, { optionKey: 'box', ratio: 6 }] },
    specAxes: [{ clientKey: 'pack', name: '单位', sortOrder: 0, options: [
      { clientKey: 'single', value: '件', sortOrder: 0 },
      { clientKey: 'box', value: '箱', sortOrder: 1 },
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
  await expect(page.getByRole('region', { name: '库存池绑定' })).toHaveCount(0)
  const rows = await page.request.get(`/api/v1/admin/product-rows?keyword=${unique}`)
  expect(rows.status()).toBe(200)
  const productId = (await rows.json()).data.rows[0].productId
  const read = await page.request.get(`/api/v1/admin/products/${productId}`)
  expect(read.status()).toBe(200)
  const detail = (await read.json()).data
  expect(detail.unitConversion.ratios.map((row: { ratio: number }) => row.ratio).sort()).toEqual([1, 6])
  const pools = await page.request.get(`/api/v1/admin/inventory/pool-bindings?productId=${productId}`)
  expect(pools.status()).toBe(200)
  const bindings = (await pools.json()).data
  expect(new Set(bindings.items.map((row: { anchorSkuId: string }) => row.anchorSkuId)).size).toBe(1)

  await page.goto('/catalog')
  await page.getByPlaceholder('名称或编号').fill(unique)
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await page.getByRole('row').filter({ hasText: unique }).getByRole('button', { name: '编辑商品', exact: true }).click()
  await expect(page.getByLabel('开启多单位换算', { exact: true })).toBeChecked()
  await expect(page.getByLabel('单位名称 2', { exact: true })).toHaveValue('箱')
  await page.getByLabel('单位换算比 2', { exact: true }).fill('8')
  await page.getByRole('button', { name: '生成 / 更新 SKU 组合', exact: true }).click()
  await expect(page.getByRole('table', { name: 'SKU 明细', exact: true })).toContainText('1 箱＝8 件')
  const previewed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/admin/products/${productId}/specs/preview`)
  await page.getByRole('button', { name: '核对规格变更影响', exact: true }).click()
  expect((await previewed).status()).toBe(200)
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/admin/products/${productId}/specs` && response.request().method() === 'PUT')
  await page.getByRole('button', { name: '确认保存规格与 SKU', exact: true }).click()
  expect((await saved).status()).toBe(200)
  const afterSave = await page.request.get(`/api/v1/admin/products/${productId}`)
  const updated = (await afterSave.json()).data
  expect(updated.skus.map((row: { skuId: string }) => row.skuId).sort()).toEqual(detail.skus.map((row: { skuId: string }) => row.skuId).sort())
  expect(updated.skus.find((row: { skuCode: string }) => row.skuCode === `${unique}_B`).unit).toEqual({ baseUnit: '件', saleUnit: '箱', ratio: 8 })
  await page.reload()
  await page.getByRole('row').filter({ hasText: unique }).getByRole('button', { name: '编辑商品', exact: true }).click()
  await expect(page.getByLabel('单位换算比 2', { exact: true })).toHaveValue('8')
  await screenshot(page, testInfo, 'automatic-unit-specifications')

})

test('real isolated database: phase2 templates, sharing cover and fixed mosaic', async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill(process.env.MALL_E2E_OWNER!)
  await page.getByLabel('密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'csrftoken')!.value
  const headers = { 'X-CSRFToken': csrf, Origin: new URL(page.url()).origin }
  const upload = await page.request.post('/api/v1/admin/assets', { headers, multipart: { kind: 'IMAGE', file: { name: `phase2_${testInfo.project.name}.png`, mimeType: 'image/png', buffer: readFileSync(new URL('../../../mini-program/assets/startup-brand.png', import.meta.url)) } } })
  expect(upload.status()).toBe(201)
  const assetId = (await upload.json()).data.assetId
  expect((await page.request.get(`/api/v1/app/assets/${assetId}/file`)).status()).toBe(404)
  await page.goto('/pages/micro')
  const name = `第二阶段布局 ${testInfo.project.name}`
  await page.getByLabel('新页面名称', { exact: true }).fill(name)
  await page.getByRole('button', { name: '创建微页面', exact: true }).click()
  await expect(page.getByLabel('页面名称', { exact: true })).toHaveValue(name)
  const id = new URL(page.url()).searchParams.get('pageId')!
  await page.getByText('主题、页面模板与组件组合', { exact: true }).click()
  await page.getByRole('button', { name: '加入品牌页头组合', exact: true }).click()
  await expect(page.locator('.home-component-list li')).toHaveCount(3)
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(page.locator('.home-component-list li')).toHaveCount(0)
  await page.getByRole('button', { name: '应用活动页面模板', exact: true }).click()
  await expect(page.getByRole('alertdialog', { name: '确认替换页面模板' })).toBeVisible()
  await expect(page.locator('.home-component-list li')).toHaveCount(0)
  await page.getByRole('button', { name: '确认替换草稿', exact: true }).click()
  await expect(page.locator('.home-component-list li')).toHaveCount(4)
  await page.getByRole('button', { name: '品牌红', exact: true }).click()
  const outline = page.locator('.home-component-list')
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await outline.getByRole('button', { name: '商品列表 显示中', exact: true }).click()
  await page.getByLabel('显示此组件', { exact: true }).uncheck()
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await outline.getByRole('button', { name: '公告栏 显示中', exact: true }).click()
  await page.getByLabel('公告文案', { exact: true }).fill('第二阶段布局验收')
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await outline.getByRole('button', { name: '固定魔方 显示中', exact: true }).click()
  for (let i = 0; i < 3; i++) await page.getByLabel('图片素材 ID', { exact: true }).nth(i).fill(assetId)
  await page.getByText('业务标签与分享资料', { exact: true }).click()
  await page.getByLabel('业务标签', { exact: true }).fill('活动,布局')
  await page.getByLabel('分享标题', { exact: true }).fill('第二阶段已发布分享')
  await page.getByLabel('分享描述', { exact: true }).fill('独立专题草稿验证')
  await page.getByLabel('分享封面素材 ID', { exact: true }).fill(assetId)
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByText('草稿已保存，线上页面未变化。', { exact: true })).toBeVisible()
  const draftResponse = await page.request.get(`/api/v1/admin/pages/${id}/draft`)
  const config = (await draftResponse.json()).data.config
  expect(config.schemaVersion).toBe(3)
  expect(config.metadata.tags).toEqual(['活动', '布局'])
  expect(config.components.every((item: any) => !item.componentId.startsWith('CAMPAIGN_'))).toBe(true)
  await page.locator('.micro-page-list > summary').click()
  await page.getByLabel('搜索页面名称', { exact: true }).fill(name)
  await page.getByLabel('筛选业务标签', { exact: true }).fill('布局')
  await page.getByRole('button', { name: '查询页面', exact: true }).click()
  await expect(page.locator('.micro-page-row')).toHaveCount(1)
  await expect(page.locator('.micro-page-row')).toContainText(name)
  await page.getByRole('button', { name: '检查并预览', exact: true }).click()
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '预览', exact: true }).click()
  await expect(page.getByText('服务端校验通过', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '发布微页面', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '确认发布', exact: true }).click()
  await expect(page.getByRole('button', { name: '当前修订已发布', exact: true })).toBeVisible()
  const published = await page.request.get(`/api/v1/app/pages/${id}?schemaVersion=3`)
  expect(published.status()).toBe(200)
  const publishedData = (await published.json()).data
  expect(publishedData.config).not.toHaveProperty('metadata')
  expect(publishedData.share).toEqual({ title: '第二阶段已发布分享', description: '独立专题草稿验证', coverUrl: `/api/v1/app/assets/${assetId}/file` })
  expect((await page.request.get(`/api/v1/app/assets/${assetId}/file`)).status()).toBe(200)
  expect((await page.request.get(`/api/v1/app/pages/${id}?schemaVersion=2`)).status()).toBe(422)
  await page.reload()
  await expect(page.locator('.home-phone-body')).toContainText('第二阶段布局验收')
  await screenshot(page, testInfo, 'phase2-real-mosaic')
})

test('real isolated database: schema2 micro-page publish, legacy fence and independent copy', async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill(process.env.MALL_E2E_OWNER!)
  await page.getByLabel('密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  await page.goto('/pages/micro')
  const name = `装修验收 ${testInfo.project.name}`
  await page.getByLabel('新页面名称', { exact: true }).fill(name)
  await page.getByRole('button', { name: '创建微页面', exact: true }).click()
  await expect(page.getByLabel('页面名称', { exact: true })).toHaveValue(name)
  const id = new URL(page.url()).searchParams.get('pageId')!
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await page.locator('.home-component-library').getByRole('button', { name: '标题文本' }).click()
  await page.getByLabel('标题', { exact: true }).fill('品牌活动真实保存')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByText('草稿已保存，线上页面未变化。', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.locator('.home-phone-body')).toContainText('品牌活动真实保存')
  await page.getByRole('button', { name: '检查并预览', exact: true }).click()
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '预览', exact: true }).click()
  await expect(page.getByText('服务端校验通过', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '发布微页面', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '确认发布', exact: true }).click()
  await expect(page.getByRole('button', { name: '当前修订已发布', exact: true })).toBeVisible()
  const current = await page.request.get(`/api/v1/app/pages/${id}?schemaVersion=2`)
  expect(current.status()).toBe(200)
  expect((await current.json()).data.config.components[0].props.text).toBe('品牌活动真实保存')
  const legacy = await page.request.get(`/api/v1/app/pages/${id}`)
  expect(legacy.status()).toBe(422)
  expect((await legacy.json()).error.code).toBe('PAGE_SCHEMA_UNSUPPORTED')
  await page.getByRole('button', { name: '复制页面', exact: true }).click()
  await page.getByLabel('副本名称', { exact: true }).fill(`${name} 副本`)
  await page.getByRole('combobox', { name: '复制来源', exact: true }).selectOption('PUBLISHED')
  await page.getByRole('button', { name: '创建独立草稿', exact: true }).click()
  await expect(page.getByLabel('页面名称', { exact: true })).toHaveValue(`${name} 副本`)
  const copiedId = new URL(page.url()).searchParams.get('pageId')!
  expect(copiedId).not.toBe(id)
  await page.reload()
  await expect(page.locator('.home-phone-body')).toContainText('品牌活动真实保存')
  const unpublished = await page.request.get(`/api/v1/app/pages/${copiedId}?schemaVersion=2`)
  expect(unpublished.status()).toBe(404)
  await screenshot(page, testInfo, 'real-micro-page-copy')
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
  await expect(editor.getByLabel('图片 1 说明', { exact: true })).toBeFocused()
  await expect(editor.getByRole('textbox', { name: '商品描述', exact: true }).locator('img[data-asset-id]')).toHaveCount(1)
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

test('real isolated database: phase3 coupon reference reports, schema4 publish and version-bound hydration', async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill(process.env.MALL_E2E_OWNER!)
  await page.getByLabel('密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  await page.goto('/pages/micro')
  const name = `第三阶段发布检查 ${testInfo.project.name}`
  await page.getByLabel('新页面名称', { exact: true }).fill(name)
  await page.getByRole('button', { name: '创建微页面', exact: true }).click()
  await expect(page.getByLabel('页面名称', { exact: true })).toHaveValue(name)
  const id = new URL(page.url()).searchParams.get('pageId')!
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await page.getByRole('button', { name: '商品营销', exact: true }).click()
  await page.locator('.home-component-library').getByRole('button', { name: '优惠券列表', exact: true }).click()
  await page.getByLabel('优惠券来源', { exact: true }).selectOption('MANUAL')
  await page.getByLabel('优惠券活动 ID', { exact: true }).fill('90000000-0000-4000-8000-000000000001')
  await page.getByLabel('优惠券活动 ID', { exact: true }).press('Tab')
  await page.getByRole('button', { name: '检查发布差异与引用', exact: true }).click()
  await expect(page.getByText(/当前存在发布阻断项/)).toBeVisible()
  await screenshot(page, testInfo, 'phase3-real-invalid-reference')
  const saved = (await (await page.request.get(`/api/v1/admin/pages/${id}/draft`)).json()).data
  expect(saved.config.schemaVersion).toBe(4)
  expect(saved.publishedVersionId).toBeNull()
  await page.getByLabel('优惠券来源', { exact: true }).selectOption('AUTO')
  await expect(page.getByText(/报告已过期/)).toBeVisible()
  await page.getByRole('button', { name: '检查发布差异与引用', exact: true }).click()
  await expect(page.getByText(/当前检查未发现发布阻断项/)).toBeVisible()
  await screenshot(page, testInfo, 'phase3-real-current-report')
  await page.getByRole('button', { name: '检查并预览', exact: true }).click()
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '预览', exact: true }).click()
  await expect(page.getByText('服务端校验通过', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '发布微页面', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', { name: '确认发布', exact: true }).click()
  await expect(page.getByRole('button', { name: '当前修订已发布', exact: true })).toBeVisible()
  await expect(page.getByText(/报告已过期/)).toBeVisible()
  const response = await page.request.get(`/api/v1/app/pages/${id}?schemaVersion=4`)
  expect(response.status()).toBe(200)
  const published = (await response.json()).data
  expect(published).not.toHaveProperty('couponData')
  expect((await page.request.get(`/api/v1/app/pages/${id}?schemaVersion=3`)).status()).toBe(422)
  const hydration = await page.request.get(`/api/v1/app/pages/${id}/coupons?versionId=${published.versionId}`)
  expect(hydration.status()).toBe(200)
  expect(hydration.headers()['cache-control']).toContain('no-store')
  const cards = (await hydration.json()).data
  expect(cards.memberId).toBeNull()
  expect(cards.versionId).toBe(published.versionId)
  expect(Object.values(cards.componentData)).toEqual([[]])
  expect((await page.request.get(`/api/v1/app/pages/${id}/coupons?versionId=90000000-0000-4000-8000-000000000001`)).status()).toBe(409)
  await page.getByLabel('页面名称', { exact: true }).fill(`${name} 更名`)
  const nameReport = page.waitForResponse(response => response.url().endsWith(`/pages/${id}/release-report`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '检查发布差异与引用', exact: true }).click()
  const nameDiff = (await (await nameReport).json()).data.diff
  expect(nameDiff.nameChanged).toBe(true)
  expect(nameDiff.updatedComponentIds).toEqual([])
  await expect(page.locator('.release-report-diff')).toContainText('页面名称变化')
  expect((await (await page.request.get(`/api/v1/app/pages/${id}?schemaVersion=4`)).json()).data.name).toBe(name)
  await screenshot(page, testInfo, 'phase3-real-coupon-report')
  await page.reload()
  await expect(page.locator('.home-component-list')).toContainText('优惠券列表')
})


test('real isolated database: store creation and account readiness persist after reload', async ({page}, testInfo) => {
  await page.goto('/')
  await page.getByLabel('账号', {exact:true}).fill(process.env.MALL_E2E_OWNER!)
  await page.getByLabel('密码', {exact:true}).fill(process.env.MALL_E2E_OWNER_PASSWORD!)
  await page.getByRole('button', {name:'登录',exact:true}).click()
  await expect(page.getByRole('button', {name:'退出',exact:true})).toBeVisible()
  await page.goto('/stores/new')
  const name = `真实联调门店_${testInfo.project.name}_${Date.now()}`
  await page.getByLabel('门店名称',{exact:true}).fill(name)
  await page.getByLabel('营业时间',{exact:true}).fill('09:00–21:00')
  await page.getByLabel('联系人',{exact:true}).fill('测试店长')
  await page.getByLabel('联系电话',{exact:true}).fill('13800000000')
  await page.getByLabel('城市',{exact:true}).fill('长沙')
  await page.getByLabel('详细地址',{exact:true}).fill('湘江路测试地址')
  await page.getByLabel('纬度',{exact:true}).fill('28.200000')
  await page.getByLabel('经度',{exact:true}).fill('112.900000')
  await page.getByRole('button',{name:'保存门店',exact:true}).click()
  await expect(page).toHaveURL(/\/stores\/[0-9a-f-]{36}$/)
  await page.reload()
  await expect(page.getByLabel('门店名称',{exact:true})).toHaveValue(name)
  const storeId = new URL(page.url()).pathname.split('/').pop()
  const persisted = await page.request.get(`/api/v1/admin/stores/${storeId}`)
  expect(persisted.status()).toBe(200)
  const store = (await persisted.json()).data
  expect(store.name).toBe(name)
  expect(store.deliveryFeeFen).toBeNull()
  await screenshot(page,testInfo,'real-store-persisted')
  await page.goto(`/stores/accounts/${storeId}`)
  await expect(page.getByRole('button',{name:'提现待配置'})).toBeDisabled()
  await expect(page.getByText('待配置',{exact:true})).toHaveCount(4)
  await screenshot(page,testInfo,'real-store-account')
})
