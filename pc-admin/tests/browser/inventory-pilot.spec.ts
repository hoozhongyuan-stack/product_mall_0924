/** Exercise the real selective component registration in main.ts, with synthetic HTTP only. */
import { expect, test } from '@playwright/test'
import { account, asset, campaign, ids, installMockApi, ok, screenshot, sku } from './fixtures'

for (const readonly of [false, true]) {
  test(`SKU table preserves combinations and ${readonly ? 'published readonly limits' : 'selected price updates'}`, async ({ page }, testInfo) => {
    const axes = [{ id: 'degree', name: '度数', options: [{ id: 'd53', value: '53°' }, { id: 'd43', value: '43°' }] }, { id: 'pack', name: '包装', options: [{ id: 'bottle', value: '单瓶' }, { id: 'case', value: '整箱' }] }]
    const codes = ['SKU-53-BOTTLE', 'SKU-53-CASE', 'SKU-43-BOTTLE', 'SKU-43-CASE']
    const items = codes.map((code, index) => ({ ...sku, skuId: `sku-${index}`, skuCode: code, listPriceFen: 10000 + index * 1000, specOptionIds: [index < 2 ? 'd53' : 'd43', index % 2 ? 'case' : 'bottle'], unit: { ...sku.unit, baseUnit: '瓶', saleUnit: index % 2 ? '箱' : '瓶', ratio: index % 2 ? 6 : 1 } }))
    const verify = await installMockApi(page, async (route, path) => {
      if (path === '/api/v1/admin/member-grades') { await ok(route, [{ id: 'normal', code: 'NORMAL', name: '普通会员', enabled: true }, { id: 'gold', code: 'GOLD', name: '金卡', enabled: true }]); return true }
      if (path !== '/api/v1/admin/products/product-a') return false
      await ok(route, { productId: 'product-a', productNo: 'BROWSER_PRODUCT', name: '多规格商品', categoryId: 'leaf', fulfillmentKind: 'SHIP', redeemValidUntil: null, status: readonly ? 'ON_SALE' : 'DRAFT', descriptionHtml: '', productRevision: 4, mainImage: null, galleryImages: [], video: null, specAxes: axes, skus: items })
      return true
    })
    await page.goto('/catalog')
    await page.getByRole('button', { name: '编辑商品', exact: true }).click()
    const table = page.getByRole('table', { name: 'SKU 明细', exact: true })
    await expect(table.getByRole('row')).toHaveCount(5)
    await expect(table.getByRole('columnheader', { name: '度数', exact: true })).toBeVisible()
    await expect(table.getByRole('columnheader', { name: '包装', exact: true })).toBeVisible()
    await expect(table).toContainText('1 箱＝6 瓶')
    const price = page.getByRole('textbox', { name: '日常价 SKU-53-BOTTLE', exact: true })
    if (readonly) {
      await expect(price).toBeDisabled()
      await expect(page.getByRole('checkbox', { name: '选择全部 SKU', exact: true })).toHaveCount(0)
    } else {
      await page.getByRole('checkbox', { name: '选择 SKU SKU-53-BOTTLE', exact: true }).check()
      await page.getByRole('textbox', { name: '批量价格（元）', exact: true }).fill('199.00')
      await page.getByRole('button', { name: '应用到所选 SKU', exact: true }).click()
      await page.locator('.el-message-box').getByRole('button', { name: '确认继续', exact: true }).click()
      await expect(price).toHaveValue('199.00')
      await expect(page.getByRole('textbox', { name: '日常价 SKU-53-CASE', exact: true })).toHaveValue('110.00')
      await page.getByRole('combobox', { name: '批量价格字段', exact: true }).selectOption('gold')
      await page.getByRole('textbox', { name: '批量价格（元）', exact: true }).fill('189.00')
      await page.getByRole('button', { name: '应用到所选 SKU', exact: true }).click()
      await expect(page.getByRole('textbox', { name: '金卡价 SKU-53-BOTTLE', exact: true })).toHaveValue('189.00')
      await expect(page.getByRole('textbox', { name: '金卡价 SKU-53-CASE', exact: true })).toHaveValue('')
    }
    const scroller = page.getByLabel('SKU 明细表，可横向滚动', { exact: true })
    expect(await scroller.evaluate(el => getComputedStyle(el).overflowX)).toBe('auto')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await screenshot(page, testInfo, readonly ? 'sku-readonly-table' : 'sku-edit-price-table')
    verify()
  })
}

test('coupon activity table fills its container and keeps readable cell spacing', async ({ page }, testInfo) => {
  const verify = await installMockApi(page, async (route, path) => {
    if (path !== '/api/v1/admin/coupon-campaigns') return false
    await ok(route, { items: [{ ...campaign(ids.a), title: '国庆节指定商品满减优惠券活动', status: 'PUBLISHED', issuedQuantity: 1, remainingQuantity: 99 }], pagination: { page: 1, pageSize: 20, total: 1 } })
    return true
  })
  await page.goto('/coupons')
  const table = page.getByRole('table')
  const pageSize = page.locator('.el-pagination__sizes .el-select')
  await expect(pageSize).toContainText('20')
  expect((await pageSize.boundingBox())!.width).toBeGreaterThanOrEqual(120)
  await expect(table).toBeVisible()
  const metrics = await table.evaluate(el => {
    const th = el.querySelector('th')!
    return { width: el.getBoundingClientRect().width, parent: el.parentElement!.clientWidth, padding: parseFloat(getComputedStyle(th).paddingLeft) }
  })
  expect(metrics.width).toBeGreaterThanOrEqual(metrics.parent - 2)
  expect(metrics.padding).toBeGreaterThanOrEqual(12)
  await expect(table).toContainText('国庆节指定商品满减优惠券活动')
  await expect(table).toContainText('99')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, testInfo, 'coupon-readable-table')
  verify()
})

test('unsaved product confirmation is an application dialog centered in the viewport', async ({ page }, testInfo) => {
  const verify = await installMockApi(page, async (route, path) => {
    if (path !== '/api/v1/admin/products/product-a') return false
    await ok(route, { productId: 'product-a', productNo: 'BROWSER_PRODUCT', name: '经典干红葡萄酒 750ml', categoryId: 'leaf', fulfillmentKind: 'SHIP', redeemValidUntil: null, status: 'DRAFT', descriptionHtml: '', productRevision: 4, mainImage: null, galleryImages: [], video: null, specAxes: [], skus: [{ ...sku, specOptionIds: [] }] })
    return true
  })
  const nativeDialogs: string[] = []
  page.on('dialog', dialog => { nativeDialogs.push(dialog.type()); void dialog.dismiss() })
  await page.goto('/catalog')
  await page.getByRole('button', { name: '编辑商品', exact: true }).click()
  await page.getByLabel('商品名称', { exact: true }).fill('保留未保存资料')
  await page.getByRole('button', { name: '返回商品列表', exact: true }).click()
  const dialog = page.locator('.el-message-box')
  await expect(dialog).toBeVisible()
  await expect(page.locator('.el-overlay-message-box')).toHaveCSS('transform', 'none')
  const bounds = (await dialog.boundingBox())!
  const viewport = page.viewportSize()!
  expect(Math.abs(bounds.x + bounds.width / 2 - viewport.width / 2)).toBeLessThan(3)
  expect(Math.abs(bounds.y + bounds.height / 2 - viewport.height / 2)).toBeLessThan(3)
  await screenshot(page, testInfo, 'centered-unsaved-confirmation')
  await dialog.getByRole('button').filter({ hasText: /继续编辑|取消|返回/ }).first().click()
  await expect(page.getByLabel('商品名称', { exact: true })).toHaveValue('保留未保存资料')
  expect(nativeDialogs).toEqual([])
  verify()
})

test('inventory pilot: production-mounted SKU dialog renders, pages and retains selected specifications', async ({ page }, testInfo) => {
  const runtimeErrors: string[] = []
  page.on('pageerror', error => runtimeErrors.push(error.message))
  page.on('console', message => { if (message.text().includes('Failed to resolve component')) runtimeErrors.push(message.text()) })
  const requests: URL[] = []
  const item = (blue = false) => ({ skuId: blue ? 'sku-blue' : 'sku-red', skuCode: blue ? 'PHONE-BLUE-512' : 'PHONE-RED-256',
    productNo: 'PHONE', productName: '演示智能手机', specs: [{ name: '颜色', value: blue ? '蓝色' : '红色' }, { name: '容量', value: blue ? '512GB' : '256GB' }],
    mainImage: asset, baseUnit: '件', saleUnit: '箱', ratio: 5, unitVersionId: 'unit1',
    warehouseStock: { warehouseId: 'warehouse1', onHandBaseUnits: 20, reservedBaseUnits: 3, availableBaseUnits: 17 } })
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/me') { await ok(route, { ...account, permissionCodes: ['inventory.read', 'inventory.manage'] }); return true }
    if (path === '/api/v1/admin/warehouses') { await ok(route, { items: [{ warehouseId: 'warehouse1', code: 'MAIN', name: '演示中心仓', enabled: true, isDefault: true }] }); return true }
    if (path === '/api/v1/admin/inventory/outbounds') { await ok(route, { items: [], page: 1, pageSize: 20, total: 0 }); return true }
    if (path === '/api/v1/admin/inventory/skus') {
      const url = new URL(route.request().url()); requests.push(url)
      const pageNumber = Number(url.searchParams.get('page'))
      await ok(route, { items: [item(pageNumber > 1 || !!url.searchParams.get('keyword'))], page: pageNumber, pageSize: 10, total: 21 }); return true
    }
    if (path === '/api/v1/admin/inventory/balances') {
      await ok(route, { items: [{ warehouseId: 'warehouse1', skuId: 'sku-blue', onHandBaseUnits: 20, reservedBaseUnits: 3, availableBaseUnits: 17 }], page: 1, pageSize: 1, total: 1 }); return true
    }
    return false
  })
  await page.goto('/inventory/outbounds')
  await expect(page.getByRole('navigation', { name: '库存功能' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '人工出库', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '新建出库单' }).click()
  await page.getByRole('button', { name: '选择商品 / SKU', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '选择商品 / SKU' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('PHONE-RED-256', { exact: false })).toBeVisible()
  await expect(dialog.getByText('颜色：红色 · 容量：256GB')).toBeVisible()
  await expect(dialog.getByText('可售 17 件')).toBeVisible()
  await expect(dialog.locator('.el-table')).toBeVisible()
  await dialog.locator('button.btn-next').click()
  await expect(dialog.getByText('PHONE-BLUE-512', { exact: false })).toBeVisible()
  await dialog.getByRole('textbox', { name: '搜索商品或 SKU' }).fill(' 蓝色 ')
  await dialog.getByRole('button', { name: '搜索', exact: true }).click()
  await expect.poll(() => requests.at(-1)?.searchParams.get('keyword')).toBe('蓝色')
  expect(requests.at(-1)?.searchParams.get('page')).toBe('1')
  await screenshot(page, testInfo, 'inventory-sku-dialog')
  await dialog.getByRole('button', { name: '选用', exact: true }).click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('.stock-document-form')).toContainText('颜色：蓝色 · 容量：512GB')
  await expect(page.locator('.inventory-line-stock')).toContainText('可售 17 件')
  await expect(page.getByRole('button', { name: '更换商品', exact: true })).toBeVisible()
  await screenshot(page, testInfo, 'inventory-selected-sku')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(runtimeErrors).toEqual([])
  verify()
})

test('inventory date panel is bounded and workspace navigation stays visible while scrolling', async ({ page }, testInfo) => {
  await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/me') { await ok(route, { ...account, permissionCodes: ['inventory.read'] }); return true }
    if (path === '/api/v1/admin/warehouses') { await ok(route, { items: [] }); return true }
    if (path === '/api/v1/admin/inventory/outbounds') { await ok(route, { items: [], page: 1, pageSize: 20, total: 0 }); return true }
    return false
  })
  await page.goto('/inventory/outbounds')
  await page.getByPlaceholder('开始日期', { exact: true }).click()
  const panel = page.locator('.inventory-date-popover')
  await expect(panel).toBeVisible()
  await expect(panel).toHaveCSS('opacity', '1')
  await expect(panel.locator('.el-picker-panel')).toBeVisible()
  const bounds = await panel.boundingBox()
  expect(bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width - 16)
  expect(bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height * .75)
  expect(bounds!.y).toBeGreaterThanOrEqual(0)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height)
  const table = panel.locator('.el-date-table').first()
  expect((await table.boundingBox())!.width).toBeLessThan(340)
  expect(await panel.locator('.el-picker-panel').evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  await expect(panel.getByText('近 7 天', { exact: true })).toBeVisible()
  await screenshot(page, testInfo, 'inventory-date-panel')
  await panel.getByText('近 7 天', { exact: true }).click()
  await expect(page.getByPlaceholder('开始日期', { exact: true })).not.toHaveValue('')
  await page.evaluate(() => { document.querySelector('.page-content')!.setAttribute('style', 'min-height:2000px'); window.scrollTo(0, 650) })
  const nav = page.locator('.workspace-navigation')
  await expect(nav).toBeVisible()
  expect((await nav.boundingBox())!.y).toBe(64)
  await screenshot(page, testInfo, 'inventory-date-navigation')
})

test('payment modes save independently and member identity uses the public number', async ({ page }, testInfo) => {
  let saved: Record<string, unknown> | null = null
  const policy = { instructions: '请核实到账', merchantAccountId: 'test-bank', revision: 1,
    wechatTimeoutMinutes: 30, offlineTimeoutMinutes: 1440, offlineEnabled: false, wechatEnabled: false,
    availablePaymentMethods: [], configured: true, wechatConfigurationStatus: 'NOT_CONFIGURED' }
  await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/me') { await ok(route, { ...account, permissionCodes: ['payment.settings.manage', 'member.read'] }); return true }
    if (path === '/api/v1/admin/payments/offline-policy') {
      if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); await ok(route, { ...policy, ...saved, revision: 2, availablePaymentMethods: ['OFFLINE', 'WECHAT'] }) }
      else await ok(route, policy)
      return true
    }
    if (path === '/api/v1/admin/members') {
      await ok(route, { items: [{ id: 'member-internal-id', memberNo: 'm20260930130512A7x', nickname: '测试会员', phone: '13800138000', avatarUrl: '/api/v1/app/member-avatars/test/file', grade: { id: 'grade', name: '普通会员' }, enabled: true, effectiveSpendFen: 0, points: { availablePoints: 0, frozenPoints: 0, debtPoints: 0 }, createdAt: '2026-09-30T05:05:12Z' }], grades: [], pagination: { page: 1, pageSize: 20, total: 1 } }); return true
    }
    return false
  })
  await page.goto('/store/payments')
  await page.getByRole('checkbox', { name: '线下支付', exact: true }).check()
  await page.getByRole('checkbox', { name: '微信支付', exact: true }).check()
  await expect(page.getByRole('checkbox', { name: '线下支付', exact: true })).toBeChecked()
  await page.getByRole('button', { name: '保存付款配置', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('付款配置已保存')
  expect(saved).toMatchObject({ offlineEnabled: true, wechatEnabled: true, expectedRevision: 1 })
  await screenshot(page, testInfo, 'payment-enabled-config')
  await page.goto('/members')
  await expect(page.getByText('测试会员', { exact: true })).toBeVisible()
  await expect(page.getByText('13800138000', { exact: true })).toBeVisible()
  await expect(page.getByText('m20260930130512A7x', { exact: true })).toBeVisible()
  await expect(page.getByRole('img', { name: '会员头像' })).toBeVisible()
  await screenshot(page, testInfo, 'member-profile-list')
})
