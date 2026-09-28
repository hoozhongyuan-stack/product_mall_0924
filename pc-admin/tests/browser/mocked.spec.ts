/** Real Vue pages and Chromium interactions; HTTP data/failures are explicitly mocked. */
import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { account, asset, ids, imageBytes, installMockApi, ok, openSection, screenshot, sku } from './fixtures'

test('mock HTTP: login and logout clear the password without remembering the account', async ({ page }) => {
  let loggedIn = false
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/me' && !loggedIn) {
      await route.fulfill({ status: 401, json: { success: false, error: { message: '请登录' } } }); return true
    }
    if (path === '/api/v1/admin/auth/login') { loggedIn = true; await ok(route, account); return true }
    if (path === '/api/v1/admin/auth/logout') { loggedIn = false; await ok(route, {}); return true }
    return false
  })
  await page.goto('/')
  await page.getByLabel('账号', { exact: true }).fill('synthetic-operator')
  await page.getByLabel('密码', { exact: true }).fill('synthetic-password')
  await expect(page.getByLabel('记住账号', { exact: true })).not.toBeChecked()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByRole('heading', { name: '欢迎登录' })).toBeVisible()
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  expect(await page.evaluate(() => localStorage.getItem('mall.admin.remembered-login'))).toBeNull()
  verify()
})

test('mock HTTP: pilot login remembers only account and reports authentication errors', async ({ page }, testInfo) => {
  let loggedIn = false
  let attempts = 0
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/me' && !loggedIn) {
      await route.fulfill({ status: 401, json: { success: false, error: { message: '请登录' } } }); return true
    }
    if (path === '/api/v1/admin/auth/login') {
      attempts++
      if (attempts === 1) await route.fulfill({ status: 401, json: { success: false, error: { message: '账号或密码不正确' } } })
      else { loggedIn = true; await ok(route, account) }
      return true
    }
    if (path === '/api/v1/admin/auth/logout') { loggedIn = false; await ok(route, {}); return true }
    return false
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '欢迎登录' })).toBeVisible()
  await expect(page.getByText('会话已失效，请重新登录。')).toHaveCount(0)
  await screenshot(page, testInfo, 'pilot-login')
  await page.getByLabel('账号', { exact: true }).fill('synthetic-operator')
  await page.getByLabel('密码', { exact: true }).fill('synthetic-password')
  await page.getByLabel('记住账号', { exact: true }).check()
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('账号或密码不正确')
  await expect(page.getByLabel('账号', { exact: true })).toHaveValue('synthetic-operator')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('mall.admin.remembered-login'))).toBe('synthetic-operator')
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('synthetic-password')
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  await page.reload()
  await expect(page.getByLabel('账号', { exact: true })).toHaveValue('synthetic-operator')
  await expect(page.getByLabel('密码', { exact: true })).toHaveValue('')
  await page.getByLabel('记住账号', { exact: true }).uncheck()
  expect(await page.evaluate(() => localStorage.getItem('mall.admin.remembered-login'))).toBeNull()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  verify()
})

test('mock HTTP: grouped navigation and focused product editor retain dirty data', async ({ page }, testInfo) => {
  const verify = await installMockApi(page, async (route, path) => {
    if (path !== '/api/v1/admin/products/product-a') return false
    await ok(route, { productId: 'product-a', productNo: 'BROWSER_PRODUCT', name: '经典干红葡萄酒 750ml', categoryId: 'leaf',
      fulfillmentKind: 'SHIP', redeemValidUntil: null, status: 'DRAFT', descriptionHtml: '', productRevision: 4,
      mainImage: null, galleryImages: [], video: null, specAxes: [], skus: [{ ...sku, specOptionIds: [] }] })
    return true
  })
  await page.goto('/')
  await openSection(page, '商品')
  await expect(page).toHaveURL(/\/catalog$/)
  await expect(page.getByRole('heading', { name: '商品管理', exact: true })).toBeVisible()
  await screenshot(page, testInfo, 'pilot-catalog-list')
  await page.getByRole('button', { name: '编辑商品', exact: true }).click()
  const workspace = page.getByRole('region', { name: '商品编辑工作区' })
  await expect(workspace).toBeVisible()
  await expect(page.getByRole('search')).toBeHidden()
  await workspace.getByLabel('商品名称', { exact: true }).fill('保留未保存的商品名称')
  page.once('dialog', dialog => dialog.dismiss())
  await page.getByRole('button', { name: '返回商品列表', exact: true }).click()
  await expect(workspace.getByLabel('商品名称', { exact: true })).toHaveValue('保留未保存的商品名称')
  await screenshot(page, testInfo, 'pilot-catalog-editor')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '返回商品列表', exact: true }).click()
  await expect(workspace).toHaveCount(0)
  await expect(page.getByRole('search')).toBeVisible()
  verify()
})

test('mock HTTP: coupon browser-back route update protects dirty details', async ({ page }, testInfo) => {
  const verify = await installMockApi(page)
  await page.goto(`/coupons/${ids.a}`)
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('活动甲')
  // Seed adjacent detail history using the real application router. The action under test is browser Back.
  await page.evaluate(async id => {
    // Use the mounted app's router; importing a bare Vite URL can create a second router after HMR.
    const root = document.querySelector('#app') as HTMLElement & { __vue_app__: { config: { globalProperties: { $router: { push(path: string): Promise<unknown> } } } } }
    await root.__vue_app__.config.globalProperties.$router.push(`/coupons/${id}`)
  }, ids.b)
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('活动乙')
  await page.getByLabel('活动名称', { exact: true }).fill('未保存的活动乙')
  page.once('dialog', dialog => dialog.dismiss())
  await page.goBack()
  await expect(page).toHaveURL(new RegExp(ids.b + '$'))
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('未保存的活动乙')
  await screenshot(page, testInfo, 'coupon-rejected-navigation')
  page.once('dialog', dialog => dialog.accept())
  await page.goBack()
  await expect(page).toHaveURL(new RegExp(ids.a + '$'))
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('活动甲')
  verify()
})

for (const target of ['home', 'micro']) {
  test(`mock HTTP: ${target} target HTTP failure and retry preserve edits`, async ({ page }, testInfo) => {
    let attempts = 0
    const verify = await installMockApi(page, async (route, path) => {
      if (path !== '/api/v1/app/categories') return false
      attempts++
      if (attempts === 1) await route.fulfill({ status: 503, json: { success: false, error: { message: '模拟目标服务故障' } } })
      else await ok(route, [{ id: 'leaf', name: '重试恢复分类' }])
      return true
    })
    await page.goto(`/pages/${target}`)
    if (target === 'micro') await page.getByRole('button', { name: /浏览器微页面/ }).click()
    await page.getByRole('button', { name: '公告栏 显示中', exact: true }).click()
    await expect(page.getByRole('alert').filter({ hasText: '部分目标列表暂不可用' })).toBeVisible()
    await page.getByLabel('公告文案').fill('重试也必须保留的编辑内容')
    await screenshot(page, testInfo, `${target}-target-error`)
    await page.getByRole('button', { name: '重试读取目标' }).click()
    await expect(page.getByText('部分目标列表暂不可用', { exact: false })).toHaveCount(0)
    await expect(page.getByLabel('公告文案')).toHaveValue('重试也必须保留的编辑内容')
    await expect(page.locator('datalist option[value="leaf"]')).toHaveAttribute('label', '重试恢复分类')
    expect(attempts).toBe(2)
    await screenshot(page, testInfo, `${target}-target-recovered`)
    verify()
  })
}

test('mock HTTP: product media upload failure, retry, and remove use shared editor', async ({ page }, testInfo) => {
  let uploads = 0
  const verify = await installMockApi(page, async (route, path) => {
    if (path !== '/api/v1/admin/assets' || route.request().method() !== 'POST') return false
    uploads++
    if (uploads === 1) await route.fulfill({ status: 503, json: { success: false, error: { message: '模拟图片上传失败' } } })
    else await ok(route, asset)
    return true
  })
  await page.goto('/catalog')
  await page.getByRole('button', { name: '新建商品', exact: true }).click()
  const editor = page.getByRole('region', { name: '商品图片与视频' })
  const mainFile = editor.locator('input[type="file"]').first()
  const upload = { name: 'browser.png', mimeType: 'image/png', buffer: imageBytes }
  await mainFile.setInputFiles(upload)
  await expect(editor.getByRole('alert')).toHaveText('模拟图片上传失败')
  await mainFile.setInputFiles(upload)
  await expect(editor.getByAltText('当前商品主图')).toBeVisible()
  await expect(editor.getByRole('alert')).toHaveCount(0)
  await screenshot(page, testInfo, 'media-upload-recovered')
  await editor.getByRole('button', { name: '移除', exact: true }).click()
  await expect(editor.getByAltText('当前商品主图')).toHaveCount(0)
  expect(uploads).toBe(2)
  verify()
})

test('mock HTTP: selected SKU CSV download protects prefixed formulas', async ({ page }, testInfo) => {
  const verify = await installMockApi(page)
  await page.goto('/catalog')
  await page.getByRole('checkbox', { name: '选择 BROWSER_SKU', exact: true }).check()
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出所选', exact: true }).click()
  const download = await downloadEvent
  const csv = await readFile((await download.path())!, 'utf8')
  expect(csv.startsWith('\uFEFF')).toBe(true)
  expect(csv).toContain('"\' \uFEFF=1+1"')
  expect(csv).toContain('"8.88"')
  expect(csv.endsWith('\r\n')).toBe(true)
  await testInfo.attach('mock-data-export.csv', { body: csv, contentType: 'text/csv' })
  await screenshot(page, testInfo, 'csv-selected-product')
  verify()
})

test('mock HTTP: workbench uses authorized real-shaped reports with error recovery', async ({ page }, testInfo) => {
  let fail = true
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['business.report.read', 'order.read'] }); return true }
    if (path.endsWith('/business-summary')) {
      if (fail) await route.fulfill({ status: 503, json: { success: false, error: { message: '经营数据暂时不可用' } } })
      else await ok(route, { from: '2026-09-01', to: '2026-09-28', timeZone: 'Asia/Shanghai', totals: { paidOrderCount: 12, paidAmountFen: 123456, refundCount: 2, refundAmountFen: 10000, netAmountFen: 113456, pointsExchangeCount: 3 }, days: [{ date: '2026-09-28', paidOrderCount: 12, paidAmountFen: 123456, refundCount: 2, refundAmountFen: 10000, netAmountFen: 113456, pointsExchangeCount: 3 }] })
      return true
    }
    return false
  })
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('经营数据暂时不可用')
  fail = false
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByLabel('区间经营合计')).toContainText('1,234.56')
  const entries = page.getByRole('region', { name: '业务入口' })
  await expect(entries.getByRole('link', { name: '订单管理' })).toBeVisible()
  await expect(entries.getByRole('link', { name: '子账号' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, testInfo, 'site-workbench')
  await page.goto('/accounts')
  await expect(page.getByText('当前账号没有此页面的读取权限。')).toBeVisible()
  verify()
})

test('mock HTTP: order query uses server fulfillment pagination and recovers empty filters', async ({ page }, testInfo) => {
  const queries: URLSearchParams[] = []
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['order.read'] }); return true }
    if (path === '/api/v1/admin/orders') {
      const params = new URL(route.request().url()).searchParams
      queries.push(params)
      await ok(route, { items: params.get('search') ? [] : [{ orderId: 'browser-order', orderNo: 'M20260928001', status: 'PAID', paymentMethod: 'OFFLINE', paymentReviewStatus: 'CONFIRMED', fulfillmentStatus: 'WAITING_SHIPMENT', payableFen: 12800, createdAt: '2026-09-28T01:00:00Z', expiresAt: '2026-09-28T02:00:00Z' }], total: params.get('search') ? 0 : 1, page: 1, pageSize: 20 }); return true
    }
    return false
  })
  await page.goto('/orders')
  await expect(page.getByText('M20260928001')).toBeVisible()
  await expect(page.getByRole('button', { name: /批量发货/ })).toHaveCount(0)
  await page.getByLabel('履约待办').selectOption('WAITING_SHIPMENT')
  await page.getByPlaceholder('搜索订单号').fill(' no-order ')
  await page.getByRole('button', { name: '查询订单' }).click()
  await expect(page.getByText('当前条件下暂无订单。可调整筛选条件后查询。')).toBeVisible()
  expect(queries.at(-1)?.get('fulfillment')).toBe('WAITING_SHIPMENT')
  expect(queries.at(-1)?.get('search')).toBe('no-order')
  await page.getByRole('button', { name: '重置', exact: true }).click()
  await expect(page.getByText('M20260928001')).toBeVisible()
  expect(queries.at(-1)?.has('fulfillment')).toBe(false)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, testInfo, 'site-orders')
  verify()
})

test('mock HTTP: member and inventory pages preserve readable empty and retry states', async ({ page }, testInfo) => {
  let memberFail = true
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['member.read', 'inventory.read'] }); return true }
    if (path === '/api/v1/admin/members') {
      if (memberFail) await route.fulfill({ status: 503, json: { success: false, error: { message: '会员服务暂不可用' } } })
      else await ok(route, { items: [], grades: [], pagination: { total: 0, page: 1, pageSize: 20 } })
      return true
    }
    if (path === '/api/v1/admin/warehouses') { await ok(route, { items: [] }); return true }
    if (path === '/api/v1/admin/inventory/balances') { await ok(route, { items: [], page: 1, pageSize: 20, total: 0 }); return true }
    return false
  })
  await page.goto('/members')
  await expect(page.getByRole('alert')).toContainText('会员服务暂不可用')
  memberFail = false
  await page.getByRole('alert').getByRole('button').click()
  await expect(page.getByText(/暂无.*会员/)).toBeVisible()
  await screenshot(page, testInfo, 'site-members-empty')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await openSection(page, '库存')
  await expect(page.getByRole('heading', { name: '库存管理' })).toBeVisible()
  await expect(page.getByRole('button', { name: '创建仓库' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, testInfo, 'site-inventory-empty')
  verify()
})

test('mock HTTP: system and marketing lists retain read-only actions and responsive layouts', async ({ page }, testInfo) => {
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['account.read', 'permission.read', 'coupon.read', 'exchange.read'] }); return true }
    if (path === '/api/v1/admin/accounts') { await ok(route, [{ ...account, displayName: '只读运营账号', permissionCodes: [] }]); return true }
    if (path === '/api/v1/admin/permission-groups') { await ok(route, [{ groupId: 'group-a', name: '只读运营', code: 'READ_ONLY', enabled: true, permissionCodes: ['order.read'] }]); return true }
    if (path === '/api/v1/admin/coupon-campaigns' || path === '/api/v1/admin/exchange-offers') { await ok(route, { items: [], pagination: { total: 0, page: 1, pageSize: 20 } }); return true }
    return false
  })
  for (const [path, title] of [['/accounts', '子账号'], ['/permission-groups', '权限组'], ['/coupons', '优惠券活动'], ['/exchange-offers', '积分商城商品']]) {
    await page.goto(path)
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    await expect(page.getByText('Select', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('status')).toHaveCount(0)
    await expect(page.getByRole('link', { name: /创建活动|添加兑换商品/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /创建账号|创建权限组/ })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await screenshot(page, testInfo, `site-${path.slice(1)}`)
  }
  verify()
})

test('mock HTTP: configuration and operations pages show honest capability states', async ({ page }, testInfo) => {
  const permissions = ['notification.read', 'code.version.read', 'payment.settings.manage', 'fulfillment.settings.manage', 'member.rules.read', 'asset.read', 'audit.read', 'aftersale.read']
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: permissions }); return true }
    if (path.endsWith('/subscription-templates')) {
      await ok(route, { sendingAvailable: false, events: ['ORDER_PAID', 'ORDER_SHIPPED', 'REFUND_SUCCEEDED'].map(eventType => ({ eventType, revision: 1, draftAppId: '', draftTemplateId: '', enabled: false, status: 'UNBOUND' })) }); return true
    }
    if (['/subscription-message-tasks', '/code-versions', '/code-sync-jobs'].some(endpoint => path.endsWith(endpoint))) { await ok(route, { items: [], nextCursor: null }); return true }
    if (path.endsWith('/payments/offline-policy')) { await ok(route, { instructions: '请确认收款信息后付款', merchantAccountId: 'synthetic-merchant', offlineTimeoutMinutes: 30, wechatTimeoutMinutes: 15, revision: 1, configured: true, availablePaymentMethods: [] }); return true }
    if (path.endsWith('/fulfillment/carriers')) { await ok(route, { items: [{ code: 'SF', name: '顺丰速运', enabled: true, revision: 1 }] }); return true }
    if (path.endsWith('/fulfillment/policy')) { await ok(route, { autoConfirmDays: 7, revision: 1 }); return true }
    if (path.endsWith('/member-rules')) { await ok(route, { revision: 1, grades: [{ id: 'grade-a', name: '普通会员', code: 'BASIC', rank: 1, minimumSpendFen: 0 }], points: { earnUnitFen: 100, earnPoints: 1, deductPoints: 100, deductFen: 100, maxPercent: 50, validDays: 365, refundValidDays: 30 } }); return true }
    if (path.endsWith('/assets')) { await ok(route, { items: [asset], total: 1, page: 1, pageSize: 20 }); return true }
    if (path.endsWith('/audit-logs')) { await ok(route, { items: [], nextCursor: null, from: '2026-09-01', to: '2026-09-28', timeZone: 'Asia/Shanghai' }); return true }
    if (path.endsWith('/aftersales')) { await ok(route, { items: [], total: 0, page: 1, pageSize: 20 }); return true }
    return false
  })
  const pages = [['/subscription-messages', '订阅消息', '发送暂未开通'], ['/subscription-message-tasks', '消息任务', '暂无匹配的消息任务。'], ['/store/code-versions', '代码版本', '微信平台尚未接入'], ['/store/payments', '付款配置', '各支付方式均未开放'], ['/fulfillment/settings', '履约设置', '自动确认收货'], ['/members/rules', '等级与积分规则', '普通会员'], ['/assets', '素材中心', 'browser.png'], ['/audit-logs', '操作日志', '当前条件下没有操作日志。'], ['/aftersales', '售后管理', '暂无']]
  for (const [path, title, content] of pages) {
    await page.goto(path)
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    await expect(page.getByText(content, { exact: false }).first()).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), path).toBe(true)
    await screenshot(page, testInfo, `site-${path.replaceAll('/', '-')}`)
  }
  verify()
})
