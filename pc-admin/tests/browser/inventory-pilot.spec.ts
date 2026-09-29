/** Exercise the real selective component registration in main.ts, with synthetic HTTP only. */
import { expect, test } from '@playwright/test'
import { account, asset, installMockApi, ok, screenshot } from './fixtures'

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
