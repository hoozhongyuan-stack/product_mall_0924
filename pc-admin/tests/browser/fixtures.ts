import { expect, type Page, type Route, type TestInfo } from '@playwright/test'

export const ids = { a: '10000000-0000-4000-8000-000000000001', b: '10000000-0000-4000-8000-000000000002' }
export const imageBytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNg+M8AAAICAQB7CYF4AAAAAElFTkSuQmCC', 'base64')
export const account = {
  accountId: 'browser-owner', loginName: 'browser-owner', displayName: '交互测试账号', kind: 'OWNER',
  enabled: true, revision: 1, groupIds: [],
  permissionCodes: ['coupon.read', 'coupon.manage', 'page.read', 'page.edit', 'catalog.read', 'catalog.write',
    'sku.status.write', 'sku.price.write', 'sku.unit.write', 'asset.read', 'asset.upload'],
}
export function campaign(id: string) {
  return { id, code: id === ids.a ? 'BROWSER_A' : 'BROWSER_B', title: id === ids.a ? '活动甲' : '活动乙',
    kind: 'FULL_REDUCTION', minGoodsFen: 10000, discountFen: 1000, productIds: [], productNames: [],
    redeemEligible: false, validFrom: '2026-01-01T00:00:00Z', validUntil: '2028-01-01T00:00:00Z',
    status: 'DRAFT', revision: 1, totalQuantity: 100, issuedQuantity: 0, remainingQuantity: 100,
    selfClaimLimit: 1, claimMode: 'BOTH', issuanceEnabled: true }
}
export const categories = [
  { id: 'root', name: '一级分类', parentId: null, status: 'ACTIVE', revision: 1 },
  { id: 'leaf', name: '二级分类', parentId: 'root', status: 'ACTIVE', revision: 1 },
]
export const sku = { skuId: 'sku-a', skuCode: 'BROWSER_SKU', skuRevision: 1, productId: 'product-a',
  productRevision: 1, productNo: 'BROWSER_PRODUCT', productName: ' \uFEFF=1+1', categoryId: 'leaf',
  fulfillmentKind: 'SHIP', specs: [], listPriceFen: 888, gradePrices: [], productStatus: 'DRAFT',
  saleStatus: 'OFF_SALE', unit: { baseUnit: '件', saleUnit: '件', ratio: 1, revision: 1 } }
export const asset = { assetId: 'browser-asset', kind: 'IMAGE', width: 1, height: 1, byteSize: imageBytes.length,
  contentType: 'image/png', originalName: 'browser.png', adminUrl: '/api/v1/admin/assets/browser-asset/file' }
export function draft(pageType = 'HOME') {
  return { pageId: ids.a, name: '浏览器微页面', revision: 1, publicationRevision: 0,
    publishedRevision: null, publishedVersionId: null,
    config: { schemaVersion: 1, pageType, theme: { pageBackgroundColor: '#F7F5F1',
      headerBackgroundColor: '#FFFFFF', brandTextColor: '#25221F' },
    components: [{ componentId: 'notice-a', type: 'NOTICE', sortOrder: 1, visible: true,
      props: { text: '保留公告', link: { type: 'CATEGORY', targetId: 'leaf' } } }] } }
}
export async function ok(route: Route, data: unknown) {
  await route.fulfill({ json: { success: true, data, requestId: 'mock-browser-request' } })
}
export async function installMockApi(page: Page, override?: (route: Route, path: string) => Promise<boolean>) {
  const unmatched: string[] = []
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (override && await override(route, path)) return
    if (path.endsWith('/file')) return route.fulfill({ body: imageBytes, contentType: 'image/png' })
    if (path === '/api/v1/admin/me') return ok(route, account)
    if (path === '/api/v1/admin/auth/csrf') return route.fulfill({
      headers: { 'Set-Cookie': 'csrftoken=browser-test-csrf; Path=/; SameSite=Lax' },
      json: { success: true, data: {} },
    })
    if (/\/coupon-campaigns\/.+\/issuances$/.test(path)) return ok(route, { items: [], pagination: { page: 1, pageSize: 20, total: 0 } })
    if (path.startsWith('/api/v1/admin/coupon-campaigns/')) return ok(route, campaign(path.split('/').at(-1)!))
    if (path === '/api/v1/admin/pages/home/draft') return ok(route, draft())
    if (path === `/api/v1/admin/pages/${ids.a}/draft`) return ok(route, draft('MICRO'))
    if (path === '/api/v1/admin/pages') return ok(route, { rows: [{ pageId: ids.a, name: '浏览器微页面',
      revision: 1, publishedRevision: null, updatedAt: '2026-01-01T00:00:00Z' }], total: 1, page: 1, pageSize: 50 })
    if (path.endsWith('/categories')) return ok(route, categories)
    if (path === '/api/v1/app/products') return ok(route, { rows: [], total: 0, page: 1, pageSize: 100 })
    if (path === '/api/v1/admin/member-grades') return ok(route, [])
    if (path === '/api/v1/admin/product-rows') return ok(route, { rows: [{ productId: sku.productId,
      productRevision: sku.productRevision, productNo: sku.productNo, name: sku.productName,
      categoryId: sku.categoryId, fulfillmentKind: sku.fulfillmentKind, status: sku.productStatus,
      mainImage: null, minListPriceFen: sku.listPriceFen, maxListPriceFen: sku.listPriceFen,
      skuCount: 1, onSaleSkuCount: 0, matchedSkuIds: [] }], total: 1, page: 1, pageSize: 20 })
    if (path === '/api/v1/admin/sku-rows') return ok(route, { rows: [sku], total: 1, page: 1, pageSize: 20 })
    unmatched.push(`${route.request().method()} ${path}`)
    await route.fulfill({ status: 501, json: { success: false, error: { code: 'UNMOCKED', message: '未定义的测试请求' } } })
  })
  return () => expect(unmatched, 'mock suites must explicitly define every API request').toEqual([])
}
export async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(name + '.png')
  await page.screenshot({ path, fullPage: true })
  await testInfo.attach(name, { path, contentType: 'image/png' })
}

export async function openSection(page: Page, name: string) {
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  const menu = page.getByRole('button', { name: '打开导航菜单' })
  if (await menu.isVisible()) {
    await menu.click()
    await page.getByRole('navigation', { name: '移动后台导航' }).getByRole('link', { name, exact: true }).click()
    await expect(page.getByRole('dialog')).toBeHidden()
  } else {
    await page.getByRole('navigation', { name: '后台主导航' }).getByRole('link', { name, exact: true }).click()
  }
}

/** Reuse the mounted router; a bare Vite import can create a second instance after HMR. */
export async function pushRoute(page: Page, path: string) {
  await page.evaluate(async target => {
    const root = document.querySelector('#app') as HTMLElement & { __vue_app__: { config: { globalProperties: { $router: { push(path: string): Promise<unknown> } } } } }
    await root.__vue_app__.config.globalProperties.$router.push(target)
  }, path)
}
