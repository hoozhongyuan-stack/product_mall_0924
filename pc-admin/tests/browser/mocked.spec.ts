/** Real Vue pages and Chromium interactions; HTTP data/failures are explicitly mocked. */
import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { account, asset, ids, imageBytes, installMockApi, ok, openSection, pushRoute, screenshot, sku } from './fixtures'

test('phase2 boundary: home rejects unknown configuration on reload', async ({ page }) => {
  const initial = (await import('./fixtures')).draft('HOME')
  let reads = 0, writes = 0
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/pages/home/draft') {
      if (route.request().method() === 'PUT') writes++
      reads++
      await ok(route, reads === 1 ? initial : { ...initial, revision: 99, config: { ...initial.config, schemaVersion: 5 } }); return true
    }
    return false
  })
  await page.goto('/pages/home')
  await expect(page.locator('.home-component-library')).toBeVisible()
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('需要更新管理端版本')
  await expect(page.locator('.home-component-library')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toBeDisabled()
  expect(writes).toBe(0)
  verify()
})

test('phase2 boundary: saved tags shrink a filtered list back to its valid page', async ({ page }) => {
  const initial = (await import('./fixtures')).draft('MICRO')
  let saved: any = { ...initial, config: { ...initial.config, schemaVersion: 3, metadata: { tags: ['活动'], share: { title: '', description: '', coverAssetId: '' } } } }
  const rows = Array.from({ length: 10 }, (_, i) => ({ pageId: `20000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`, name: `活动 ${i + 1}`, revision: 1, publishedRevision: null, tags: ['活动'] }))
  const verify = await installMockApi(page, async (route, path) => {
    if (path === `/api/v1/admin/pages/${ids.a}/draft`) {
      if (route.request().method() === 'PUT') { const body = route.request().postDataJSON(); saved = { ...saved, revision: 2, config: body.config } }
      await ok(route, saved); return true
    }
    if (path === '/api/v1/admin/pages') {
      const pageNo = Number(new URL(route.request().url()).searchParams.get('page') || 1)
      const stillMatches = saved.config.metadata.tags.includes('活动')
      await ok(route, { rows: pageNo === 1 ? rows : stillMatches ? [{ pageId: ids.a, name: saved.name, revision: 1, publishedRevision: null, tags: ['活动'] }] : [], page: pageNo, pageSize: 10, total: stillMatches ? 11 : 10 }); return true
    }
    return false
  })
  await page.goto(`/pages/micro?pageId=${ids.a}`)
  await expect(page.getByRole('heading', { name: '即时效果', exact: true })).toBeVisible()
  await page.locator('.micro-page-list > summary').click()
  await page.getByLabel('筛选业务标签', { exact: true }).fill('活动')
  await page.getByRole('button', { name: '查询页面', exact: true }).click()
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  await expect(page.locator('.micro-page-row')).toHaveCount(1)
  await page.getByText('业务标签与分享资料', { exact: true }).click()
  await page.getByLabel('业务标签', { exact: true }).fill('其它')
  await page.getByLabel('业务标签', { exact: true }).press('Tab')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByText('草稿已保存，线上页面未变化。', { exact: true })).toBeVisible()
  await expect(page.locator('.micro-page-row')).toHaveCount(10)
  verify()
})

test('phase2 micro editor: saved search tags, share metadata and spacer', async ({ page }, testInfo) => {
  const initial = (await import('./fixtures')).draft('MICRO')
  let saved: any = initial
  let filtered = false
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/pages') {
      const url = new URL(route.request().url())
      filtered = url.searchParams.get('q') === '浏览器' && url.searchParams.get('tag') === '活动'
      await ok(route, { rows: filtered ? [{ pageId: ids.a, name: saved.name, revision: saved.revision, publishedRevision: null, updatedAt: '2026-10-10', tags: ['活动'] }] : [], page: 1, pageSize: 10, total: filtered ? 1 : 0 }); return true
    }
    if (path === `/api/v1/admin/pages/${ids.a}/draft`) {
      if (route.request().method() === 'PUT') {
        const body = route.request().postDataJSON()
        saved = { ...saved, name: body.name, revision: saved.revision + 1, config: body.config }
      }
      await ok(route, saved); return true
    }
    return false
  })
  await page.goto(`/pages/micro?pageId=${ids.a}`)
  await expect(page.getByRole('heading', { name: '即时效果', exact: true })).toBeVisible()
  await page.locator('.micro-page-list > summary').click()
  await page.getByLabel('搜索页面名称', { exact: true }).fill('浏览器')
  await page.getByLabel('筛选业务标签', { exact: true }).fill('活动')
  await page.getByRole('button', { name: '查询页面', exact: true }).click()
  await expect(page.locator('.micro-page-row')).toContainText('活动')
  expect(filtered).toBe(true)
  await page.getByText('业务标签与分享资料', { exact: true }).click()
  await page.getByLabel('业务标签', { exact: true }).fill('活动,品牌')
  await page.getByLabel('分享标题', { exact: true }).fill('夏日品牌活动')
  await page.getByLabel('分享描述', { exact: true }).fill('精选推荐与活动导购')
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await page.getByRole('button', { name: '布局辅助', exact: true }).click()
  await page.locator('.home-component-library').getByRole('button', { name: '辅助空白', exact: true }).click()
  await expect(page.locator('.home-component-list')).toContainText('辅助空白')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByText('草稿已保存，线上页面未变化。', { exact: true })).toBeVisible()
  expect(saved.config.schemaVersion).toBe(3)
  expect(saved.config.metadata.tags).toEqual(['活动', '品牌'])
  await page.reload()
  await page.getByText('业务标签与分享资料', { exact: true }).click()
  await expect(page.getByLabel('分享标题', { exact: true })).toHaveValue('夏日品牌活动')
  await expect(page.getByLabel('分享描述', { exact: true })).toHaveValue('精选推荐与活动导购')
  await screenshot(page, testInfo, 'phase2-micro-editor')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  verify()
})

test('micro editor: immediate selection, copy history, hotzone drawing and persisted page copy', async ({ page }, testInfo) => {
  const initial = (await import('./fixtures')).draft('MICRO')
  let saved = initial
  let copied: typeof initial | null = null
  const verify = await installMockApi(page, async (route, path) => {
    if (path === `/api/v1/admin/pages/${ids.a}/draft`) {
      if (route.request().method() === 'PUT') {
        const body = route.request().postDataJSON()
        saved = { ...saved, name: body.name, revision: saved.revision + 1, config: body.config }
      }
      await ok(route, saved); return true
    }
    if (path === `/api/v1/admin/pages/${ids.a}/copy`) {
      const body = route.request().postDataJSON()
      expect(body.source).toBe('DRAFT')
      expect(body.expectedRevision).toBe(saved.revision)
      expect(route.request().headers()['idempotency-key']).toBeTruthy()
      copied = { ...saved, pageId: ids.b, name: body.name, revision: 1, publishedRevision: null,
        config: { ...saved.config, components: saved.config.components.map((item, i) => ({ ...item, componentId: `copy-${i}` })) } }
      await ok(route, copied); return true
    }
    if (path === `/api/v1/admin/pages/${ids.b}/draft` && copied) { await ok(route, copied); return true }
    return false
  })
  await page.goto(`/pages/micro?pageId=${ids.a}`)
  await expect(page.getByRole('heading', { name: '即时效果', exact: true })).toBeVisible()
  const library = page.locator('.home-component-library')
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await library.getByRole('button', { name: '标题文本' }).click()
  await page.getByLabel('标题', { exact: true }).fill('品牌专区')
  await expect(page.locator('.home-phone-body')).toContainText('品牌专区')
  await page.getByRole('button', { name: '复制组件', exact: true }).click()
  await expect(page.locator('.home-component-list li')).toHaveCount(3)
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(page.locator('.home-component-list li')).toHaveCount(2)
  await page.getByRole('button', { name: '重做', exact: true }).click()
  await expect(page.locator('.home-component-list li')).toHaveCount(3)
  if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
  await page.getByRole('button', { name: '内容', exact: true }).click()
  await library.getByRole('button', { name: '图片热区' }).click()
  await page.getByLabel('图片素材 ID', { exact: true }).fill('browser-asset')
  const canvas = page.getByLabel('热区绘制画布')
  await canvas.scrollIntoViewIfNeeded()
  const bounds = await canvas.boundingBox()
  expect(bounds).toBeTruthy()
  await page.mouse.move(bounds!.x + bounds!.width * .2, bounds!.y + bounds!.height * .2)
  await page.mouse.down()
  await page.mouse.move(bounds!.x + bounds!.width * .6, bounds!.y + bounds!.height * .6)
  await page.mouse.up()
  await expect(page.getByText('已配置 1 / 20 个区域')).toBeVisible()
  await canvas.getByRole('button', { name: '点击区域 1，方向键移动', exact: true }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByLabel('x', { exact: true })).toHaveValue('0.21')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByText('草稿已保存，线上页面未变化。', { exact: true })).toBeVisible()
  expect(saved.config.schemaVersion).toBe(2)
  await page.reload()
  await expect(page.locator('.home-phone-body')).toContainText('品牌专区')
  await page.getByRole('button', { name: '复制页面', exact: true }).click()
  await page.getByLabel('副本名称', { exact: true }).fill('品牌专区副本')
  await page.getByRole('button', { name: '创建独立草稿', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(ids.b))
  await expect(page.getByLabel('页面名称', { exact: true })).toHaveValue('品牌专区副本')
  await expect(page.getByText('尚未发布', { exact: true })).toBeVisible()
  await screenshot(page, testInfo, 'micro-editor-enhanced')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  verify()
})

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
  await page.getByRole('button', { name: '返回商品列表', exact: true }).click()
  await page.locator('.el-message-box:visible').getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(workspace.getByLabel('商品名称', { exact: true })).toHaveValue('保留未保存的商品名称')
  await screenshot(page, testInfo, 'pilot-catalog-editor')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('button', { name: '返回商品列表', exact: true }).click()
  await page.locator('.el-message-box:visible').getByRole('button', { name: '放弃修改并离开', exact: true }).click()
  await expect(workspace).toHaveCount(0)
  await expect(page.getByRole('search')).toBeVisible()
  verify()
})

test('mock HTTP: coupon browser-back route update protects dirty details', async ({ page }, testInfo) => {
  const verify = await installMockApi(page)
  await page.goto(`/coupons/${ids.a}`)
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('活动甲')
  // Seed adjacent detail history using the real application router. The action under test is browser Back.
  await pushRoute(page, `/coupons/${ids.b}`)
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('活动乙')
  await page.getByLabel('活动名称', { exact: true }).fill('未保存的活动乙')
  const rejectedBack = page.goBack()
  await page.locator('.el-message-box:visible').getByRole('button', { name: '继续处理', exact: true }).click()
  await rejectedBack
  await expect(page.locator('.el-message-box')).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(ids.b + '$'))
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('未保存的活动乙')
  await screenshot(page, testInfo, 'coupon-rejected-navigation')
  const acceptedBack = page.goBack()
  await page.locator('.el-message-box:visible').getByRole('button', { name: '离开并稍后核查', exact: true }).click()
  await acceptedBack
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
    if (target === 'micro' && testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
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

test('mock HTTP: selected SPU CSV download protects prefixed formulas', async ({ page }, testInfo) => {
  const verify = await installMockApi(page)
  await page.goto('/catalog')
  await page.getByRole('checkbox', { name: '选择商品 BROWSER_PRODUCT', exact: true }).check()
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出所选商品', exact: true }).click()
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
  await expect(page.getByLabel('区间经营合计')).not.toBeVisible()
  await page.getByText('区间经营统计与每日明细', { exact: true }).click()
  await expect(page.getByLabel('区间经营合计')).toContainText('1,234.56')
  await expect(page.getByRole('region', { name: '常用工作' }).getByRole('link', { name: '订单管理' })).toBeVisible()
  await page.getByText('全部业务入口', { exact: true }).click()
  const entries = page.locator('.dashboard-links')
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
  await expect(page.getByRole('heading', { name: '库存查询' })).toBeVisible()
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
    if (['/subscription-message-tasks', '/code-versions', '/code-sync-jobs', '/code-release/uploads', '/code-release/reviews'].some(endpoint => path.endsWith(endpoint))) { await ok(route, { items: [], nextCursor: null }); return true }
    if (path.endsWith('/integrations/wechat-open-platform')) { await ok(route, { componentAppId: '', developerAppId: '', redirectUri: '', configured: false, ticketReceived: false, revision: 0 }); return true }
    if (path.endsWith('/code-release/readiness')) { await ok(route, { appId: null, versionId: null, uploadKey: { configured: false, revision: 0, appId: null }, checks: [{ code: 'THIRD_PARTY_AUTH', status: 'UNVERIFIED', title: '微信第三方授权', detail: '尚未接入微信第三方平台授权和代码管理权限检测。' }] }); return true }
    if (path.endsWith('/payments/offline-policy')) { await ok(route, { instructions: '请确认收款信息后付款', merchantAccountId: 'synthetic-merchant', offlineTimeoutMinutes: 30, wechatTimeoutMinutes: 15, revision: 1, configured: true, availablePaymentMethods: [] }); return true }
    if (path.endsWith('/fulfillment/carriers')) { await ok(route, { items: [{ code: 'SF', name: '顺丰速运', enabled: true, revision: 1 }] }); return true }
    if (path.endsWith('/fulfillment/policy')) { await ok(route, { autoConfirmDays: 7, revision: 1 }); return true }
    if (path.endsWith('/member-rules')) { await ok(route, { revision: 1, grades: [{ id: 'grade-a', name: '普通会员', code: 'BASIC', rank: 1, minimumSpendFen: 0 }], points: { earnUnitFen: 100, earnPoints: 1, deductPoints: 100, deductFen: 100, maxPercent: 50, validDays: 365, refundValidDays: 30 } }); return true }
    if (path.endsWith('/assets')) { await ok(route, { items: [asset], total: 1, page: 1, pageSize: 20 }); return true }
    if (path.endsWith('/audit-logs')) { await ok(route, { items: [], nextCursor: null, from: '2026-09-01', to: '2026-09-28', timeZone: 'Asia/Shanghai' }); return true }
    if (path.endsWith('/aftersales')) { await ok(route, { items: [], total: 0, page: 1, pageSize: 20 }); return true }
    return false
  })
  const pages = [['/subscription-messages', '订阅消息', '发送暂未开通'], ['/subscription-message-tasks', '消息任务', '暂无匹配的消息任务。'], ['/store/code-versions', '代码版本', '上传准备'], ['/store/payments', '付款配置', '各支付方式均未开放'], ['/fulfillment/settings', '履约设置', '自动确认收货'], ['/members/rules', '等级与积分规则', '普通会员'], ['/assets', '素材中心', 'browser.png'], ['/audit-logs', '操作日志', '当前条件下没有操作日志。'], ['/aftersales', '售后管理', '暂无']]
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

const wechatConfig = { revision: 0, source: 'ENV', appId: '', secretConfigured: false, keyAvailable: true,
  identityBinding: { status: 'EMPTY', appId: null }, paymentAppIdStatus: 'NOT_CONFIGURED', notificationsStatus: 'NOT_VERIFIED', lastCheck: null }
const wechatBase = '/api/v1/admin/integrations/wechat-mini-program'

test('mock HTTP: code release checks and key confirmation stay clear on desktop and mobile', async ({ page }, testInfo) => {
  const appId = 'wx0123456789abcdef'
  let configured = false
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['code.version.read', 'code.release.manage'] }); return true }
    if (['/code-versions', '/code-sync-jobs', '/code-release/uploads', '/code-release/reviews'].some(endpoint => path.endsWith(endpoint))) { await ok(route, { items: [], nextCursor: null }); return true }
    if (path.endsWith('/integrations/wechat-open-platform')) { await ok(route, { componentAppId: '', developerAppId: '', redirectUri: '', configured: false, ticketReceived: false, revision: 0 }); return true }
    if (path.endsWith('/code-release/readiness')) {
      await ok(route, { appId, versionId: null, uploadKey: { configured, revision: Number(configured), appId: configured ? appId : null },
        checks: [
          { code: 'APP_ID', status: 'PASS', title: 'AppID', detail: '已配置小程序 AppID。' },
          { code: 'SOURCE_PACKAGE', status: 'BLOCKED', title: '代码包', detail: '当前没有完整可读取的不可变代码包。' },
          { code: 'UPLOAD_KEY', status: configured ? 'PASS' : 'BLOCKED', title: '代码上传密钥', detail: configured ? '已加密保存。' : '请上传代码上传私钥。' },
          { code: 'PLATFORM_INTEGRATION', status: 'BLOCKED', title: '微信第三方平台接入', detail: '当前后台尚未接入微信第三方平台。' },
          { code: 'THIRD_PARTY_AUTH', status: 'UNVERIFIED', title: '微信第三方授权', detail: '尚未接入授权和权限检测。' },
        ] }); return true
    }
    if (path.endsWith('/auth/confirm')) { await ok(route, { confirmationToken: 'synthetic-token' }); return true }
    if (path.endsWith('/code-release/upload-key')) {
      expect(route.request().headers()['x-action-confirmation']).toBe('synthetic-token')
      expect(JSON.parse(route.request().postData() || '{}')).toMatchObject({ appId, expectedRevision: 0 })
      configured = true
      await ok(route, { configured: true, revision: 1, appId }); return true
    }
    return false
  })
  await page.goto('/store/code-versions')
  await expect(page.getByRole('heading', { name: '上传准备' })).toBeVisible()
  await expect(page.getByText('已满足', { exact: true })).toBeVisible()
  await expect(page.getByText('未满足', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('heading', { name: '自动提审与发布' })).not.toBeVisible()
  await screenshot(page, testInfo, 'code-release-readiness-before')
  await page.getByLabel('选择密钥文件').setInputFiles({ name: 'synthetic.key', mimeType: 'text/plain', buffer: Buffer.from('synthetic-browser-key') })
  await page.getByRole('button', { name: '上传代码密钥' }).click()
  await page.getByLabel('输入当前密码确认替换密钥').fill('synthetic-password')
  await page.getByRole('button', { name: '确认保存' }).click()
  await expect(page.getByText('密钥已保存。保存只证明配置完成', { exact: false })).toBeVisible()
  await expect(page.getByText('已配置，可替换', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('synthetic-browser-key')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, testInfo, 'code-release-readiness-after')
  verify()
})

test('mock HTTP: direct code upload remains available without third-party authorization', async ({ page }, testInfo) => {
  const appId = 'wx0123456789abcdef'
  const versionId = '11111111-1111-4111-8111-111111111111'
  let submitted = false
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['code.version.read', 'code.release.manage'] }); return true }
    if (path.endsWith('/code-versions')) { await ok(route, { items: [{ versionId, versionLabel: 'source-1', sourceRevision: 'a'.repeat(40), sourceDigest: 'b'.repeat(64), packageSha256: 'c'.repeat(64), packageBytes: 1024, fileCount: 3, storageStatus: 'STORED_UNVERIFIED', platformStatus: 'NOT_CONFIGURED', createdAt: '2026-09-29T00:00:00Z', completedAt: '2026-09-29T00:00:00Z', failureCode: '' }], nextCursor: null }); return true }
    if (path.endsWith('/code-sync-jobs') || path.endsWith('/code-release/reviews')) { await ok(route, { items: [], nextCursor: null }); return true }
    if (path.endsWith('/code-release/uploads')) {
      if (route.request().method() === 'POST') {
        expect(route.request().postDataJSON()).toMatchObject({ versionId, channel: 'CI_DIRECT', version: '1.0.1' })
        expect(route.request().headers()['idempotency-key']).toBeTruthy()
        submitted = true
        await ok(route, { taskId: '22222222-2222-4222-8222-222222222222', versionId, version: '1.0.1', channel: 'CI_DIRECT', status: 'PENDING', failureCode: '', resolutionNote: '', reviewAvailable: false, createdAt: '2026-09-29T00:00:00Z' }); return true
      }
      await ok(route, { items: submitted ? [{ taskId: '22222222-2222-4222-8222-222222222222', versionId, version: '1.0.1', channel: 'CI_DIRECT', status: 'PENDING', failureCode: '', resolutionNote: '', reviewAvailable: false, createdAt: '2026-09-29T00:00:00Z' }] : [], nextCursor: null }); return true
    }
    if (path.endsWith('/code-release/readiness')) { await ok(route, { appId, versionId, egressIp: '8.152.204.21', uploadKey: { configured: true, revision: 2, appId }, developerAppId: null, developerUploadKey: { configured: false, revision: 0, appId: null }, checks: [
      { code: 'APP_ID', status: 'PASS', title: 'AppID', detail: '已配置' },
      { code: 'SOURCE_PACKAGE', status: 'PASS', title: '代码包', detail: '已准备' },
      { code: 'RELEASE_CONFIG', status: 'PASS', title: '发布配置', detail: '已准备' },
      { code: 'UPLOAD_KEY', status: 'PASS', title: '代码上传密钥', detail: '已保存' },
      { code: 'PLATFORM_INTEGRATION', status: 'BLOCKED', title: '第三方平台', detail: '未授权' },
    ] }); return true }
    if (path.endsWith('/code-release/domain-check')) { expect(route.request().postDataJSON()).toEqual({ versionId }); await ok(route, { status: 'BLOCKED', detail: '微信尚未配置所需 request 合法域名。', missingRequestDomains: ['https://api.example.com'], checkedAt: '2026-09-30T00:00:00Z' }); return true }
    if (path.endsWith('/auth/confirm')) { expect(route.request().postDataJSON()).toMatchObject({ action: 'code.release.upload', objectId: `${appId}:${versionId}`, revision: 2 }); await ok(route, { confirmationToken: 'synthetic-token' }); return true }
    return false
  })
  await page.goto('/store/code-versions')
  await expect(page.getByText('服务器出口 IP：')).toContainText('8.152.204.21')
  await expect(page.getByRole('button', { name: '上传到微信开发版本' })).toBeEnabled()
  await page.getByRole('button', { name: '检查所选代码包的域名' }).click()
  await expect(page.getByText('请在微信公众平台添加 request 合法域名：')).toContainText('https://api.example.com')
  await expect(page.getByRole('button', { name: '上传到微信开发版本' })).toBeEnabled()
  await screenshot(page, testInfo, 'direct-code-upload-ready')
  await page.locator('[data-test="ci-direct-version"]').fill('1.0.1')
  await page.locator('[data-test="ci-direct-description"]').fill('开发版验证')
  await page.locator('[data-test="ci-direct-password"]').fill('synthetic-password')
  await page.getByRole('button', { name: '上传到微信开发版本' }).click()
  await expect(page.getByText('开发版本上传任务', { exact: false })).toBeVisible()
  expect(submitted).toBe(true)
  await expect(page.getByRole('button', { name: '上传到微信开发版本' })).toBeDisabled()
  await expect(page.getByText('已有上传任务正在执行')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, testInfo, 'direct-code-upload-pending')
  verify()
})

test('mock HTTP: WeChat credentials are confirmed, stored without echo, and checked only after save', async ({ page }, testInfo) => {
  let saved: Record<string, unknown> = { ...wechatConfig }
  const writes: { path: string; body: Record<string, unknown> }[] = []
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['wechat.integration.read', 'wechat.integration.manage', 'catalog.read'] }); return true }
    if (path === '/api/v1/admin/auth/confirm') { await ok(route, { confirmationToken: 'synthetic-confirmation' }); return true }
    if (path === wechatBase && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON(); writes.push({ path, body })
      expect(route.request().headers()['x-action-confirmation']).toBe('synthetic-confirmation')
      saved = { ...saved, source: 'MANAGED', revision: Number(saved.revision) + 1, appId: body.appId, secretConfigured: true, lastCheck: null }
      await ok(route, saved); return true
    }
    if (path === wechatBase + '/test') {
      writes.push({ path, body: route.request().postDataJSON() })
      saved = { ...saved, lastCheck: { revision: saved.revision, status: 'SUCCESS', code: 'OK', checkedAt: '2026-09-28T01:00:00Z' } }
      await ok(route, saved); return true
    }
    if (path === wechatBase) { await ok(route, saved); return true }
    return false
  })
  await page.goto('/store/wechat-integration')
  await expect(page.getByRole('heading', { name: '小程序接入', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '校验已保存配置', exact: true })).toBeDisabled()
  await page.getByLabel('小程序 AppID', { exact: true }).fill('wxABCDEF0123456789')
  await page.getByLabel('新的 AppSecret', { exact: true }).fill(' synthetic-opaque-secret ')
  const rejectedRoute = pushRoute(page, '/catalog')
  await page.locator('.el-message-box:visible').getByRole('button', { name: '取消', exact: true }).click()
  await rejectedRoute
  await expect(page).toHaveURL(/wechat-integration$/)
  await expect(page.getByLabel('新的 AppSecret', { exact: true })).toHaveValue(' synthetic-opaque-secret ')
  await page.getByRole('button', { name: '保存配置', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill('synthetic-password')
  await page.getByRole('button', { name: '确认保存', exact: true }).click()
  await expect(page.getByText('配置已保存。', { exact: false })).toBeVisible()
  expect(writes[0].body).toEqual({ expectedRevision: 0, appId: 'wxABCDEF0123456789', secretAction: 'REPLACE', appSecret: ' synthetic-opaque-secret ' })
  await expect(page.getByLabel('新的 AppSecret', { exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain('synthetic-opaque-secret')
  await page.getByRole('button', { name: '校验已保存配置', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill('synthetic-password')
  await page.getByRole('button', { name: '确认校验', exact: true }).click()
  await expect(page.getByText('凭据校验通过', { exact: true })).toBeVisible()
  expect(writes[1]).toEqual({ path: wechatBase + '/test', body: { expectedRevision: 1 } })
  await expect(page.getByText('应用凭据调用成功。真实登录与真机验收仍需单独完成。')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await screenshot(page, testInfo, 'wechat-saved-and-checked')
  verify()
})

test('mock HTTP: WeChat conflict preserves input and blocks another write until a fresh read', async ({ page }, testInfo) => {
  let revision = 2, writes = 0
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: ['wechat.integration.read', 'wechat.integration.manage'] }); return true }
    if (path === '/api/v1/admin/auth/confirm') { await ok(route, { confirmationToken: 'synthetic-confirmation' }); return true }
    if (path === wechatBase && route.request().method() === 'PUT') {
      writes++; revision = 3
      await route.fulfill({ status: 409, json: { success: false, error: { code: 'REVISION_CONFLICT', message: '服务端内部信息不应回显' } } }); return true
    }
    if (path === wechatBase) { await ok(route, { ...wechatConfig, revision, source: 'MANAGED', appId: 'wx0123456789abcdef', secretConfigured: true }); return true }
    return false
  })
  await page.goto('/store/wechat-integration')
  await page.getByText('替换密钥', { exact: true }).click()
  await expect(page.getByRole('radio', { name: '替换密钥', exact: true })).toBeChecked()
  await page.getByLabel('新的 AppSecret', { exact: true }).fill('synthetic-replacement')
  await page.getByRole('button', { name: '保存配置', exact: true }).click()
  await page.getByLabel('当前账号密码', { exact: true }).fill('synthetic-password')
  await page.getByRole('button', { name: '确认保存', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('配置已被修改')
  await expect(page.getByText('服务端内部信息不应回显')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存配置', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '校验已保存配置', exact: true })).toBeDisabled()
  await expect(page.getByLabel('新的 AppSecret', { exact: true })).toHaveValue('synthetic-replacement')
  await page.getByRole('button', { name: '重新读取', exact: true }).click()
  await expect(page.getByText('请核对后重新确认', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '保存配置', exact: true })).toBeEnabled()
  expect(writes).toBe(1)
  await screenshot(page, testInfo, 'wechat-conflict-reconciled')
  verify()
})

test('mock HTTP: WeChat read-only and forbidden pages cannot trigger credentials actions', async ({ page }) => {
  let read = true, reads = 0
  const verify = await installMockApi(page, async (route, path) => {
    if (path.endsWith('/me')) { await ok(route, { ...account, permissionCodes: read ? ['wechat.integration.read'] : ['wechat.integration.manage'] }); return true }
    if (path === wechatBase) { reads++; await ok(route, { ...wechatConfig, appId: 'legacy-environment-id', keyAvailable: false }); return true }
    return false
  })
  await page.goto('/store/wechat-integration')
  await expect(page.getByLabel('小程序 AppID', { exact: true })).toHaveValue('legacy-environment-id')
  await expect(page.getByLabel('小程序 AppID', { exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '保存配置', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '校验已保存配置', exact: true })).toHaveCount(0)
  read = false
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('没有此页面的读取权限')
  expect(reads).toBe(1)
  verify()
})

test('mock HTTP: product rich description uses shared assets and preserves revision conflict edits', async ({ page }, testInfo) => {
  let writes = 0
  let submitted: Record<string, unknown> = {}
  const product = { productId: 'product-a', productNo: 'BROWSER_PRODUCT', name: '经典干红葡萄酒 750ml', categoryId: 'leaf',
    fulfillmentKind: 'SHIP', redeemValidUntil: null, status: 'DRAFT', descriptionHtml: '', productRevision: 4,
    mainImage: null, galleryImages: [], video: null, specAxes: [], skus: [{ ...sku, specOptionIds: [] }] }
  const verify = await installMockApi(page, async (route, path) => {
    if (path === '/api/v1/admin/assets') {
      await ok(route, { items: [{ ...asset, assetId: ids.a, adminUrl: `/api/v1/admin/assets/${ids.a}/file`,
        originalName: '产地介绍.png', createdAt: '2026-09-29T01:00:00Z', availability: 'READY', bindingStatus: 'UNBOUND', sha256: 'synthetic' }], total: 1, page: 1, pageSize: 20 }); return true
    }
    if (path !== '/api/v1/admin/products/product-a') return false
    if (route.request().method() === 'PATCH') {
      submitted = route.request().postDataJSON(); writes++
      if (writes === 1) await route.fulfill({ status: 409, json: { success: false, error: { code: 'REVISION_CONFLICT', message: '配置已变化' } } })
      else await ok(route, { ...product, descriptionHtml: submitted.descriptionHtml, productRevision: 5 })
    } else await ok(route, product)
    return true
  })
  await page.goto('/catalog')
  await page.getByRole('button', { name: '编辑商品', exact: true }).click()
  const description = page.getByRole('region', { name: '商品描述', exact: true })
  const textbox = description.getByRole('textbox', { name: '商品描述', exact: true })
  await textbox.fill('产地与酿造工艺')
  await textbox.press('ControlOrMeta+A')
  await description.getByRole('button', { name: '二级标题', exact: true }).click()
  await expect(textbox.locator('h2')).toHaveText('产地与酿造工艺')
  await textbox.locator('h2').click()
  await textbox.press('End')
  await description.getByRole('button', { name: '从素材中心选择', exact: true }).click()
  await page.getByRole('dialog', { name: '选择素材', exact: true }).getByRole('button', { name: '选择素材', exact: true }).click()
  await description.getByLabel('图片 1 说明', { exact: true }).fill('商品产地示意')
  await expect(description.getByLabel('图片 1 说明', { exact: true })).toBeFocused()
  await expect(description.getByRole('textbox', { name: '商品描述', exact: true }).locator('img[data-asset-id]')).toHaveCount(1)
  await description.getByRole('button', { name: '手机预览', exact: true }).click()
  await expect(description.getByLabel('手机宽度预览').getByAltText('商品产地示意')).toBeVisible()
  await page.getByRole('button', { name: '保存商品资料', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('已保留当前表单')
  await expect(textbox).toContainText('产地与酿造工艺')
  expect(submitted.expectedRevision).toBe(4)
  expect(String(submitted.descriptionHtml)).toContain(`data-asset-id="${ids.a}"`)
  expect(String(submitted.descriptionHtml)).not.toContain('src=')
  await page.getByRole('button', { name: '返回商品列表', exact: true }).click()
  await page.locator('.el-message-box:visible').getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(textbox).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0) })
  await screenshot(page, testInfo, 'product-description-editor')
  verify()
})

for (const pageType of ['HOME', 'MICRO']) {
  test(`phase3 ${pageType}: coupon config and revision-bound release reports`, async ({ page }, testInfo) => {
    const initial = (await import('./fixtures')).draft(pageType)
    let saved: any = { ...initial, config: { ...initial.config, components: [] } }
    let reportReads = 0, publishWrites = 0
    const base = pageType === 'HOME' ? '/api/v1/admin/pages/home' : `/api/v1/admin/pages/${ids.a}`
    const verify = await installMockApi(page, async (route, path) => {
      if (path === '/api/v1/admin/pages/capabilities') { await ok(route, { runtimeSchemaVersion: 4 }); return true }
      if (path === `${base}/draft`) {
        if (route.request().method() === 'PUT') { const body = route.request().postDataJSON(); saved = { ...saved, config: body.config, revision: saved.revision + 1 } }
        await ok(route, saved); return true
      }
      if (path === '/api/v1/admin/pages/coupon-preview') { await ok(route, { coupons: [] }); return true }
      if (path === `${base}/release-report`) {
        reportReads++
        expect(route.request().postDataJSON()).toEqual({ expectedRevision: saved.revision, expectedPublicationRevision: saved.publicationRevision })
        await ok(route, { pageId: saved.pageId, revision: saved.revision, publicationRevision: saved.publicationRevision, publishedVersionId: null, schemaVersion: 4, runtimeSchemaVersion: 4, runtimeSupported: true,
          diff: { addedComponentIds: saved.config.components.map((item: any) => item.componentId), removedComponentIds: [], updatedComponentIds: [], orderChanged: false, themeChanged: false, metadataChanged: false },
          issues: [{ path: 'components[0].props.campaignIds[0]', componentId: saved.config.components[0].componentId, code: 'PUBLISH_TARGET_INVALID', severity: 'ERROR', message: '优惠券活动尚未公开。' }], canPublish: false }); return true
      }
      if (path.endsWith('/publish')) { publishWrites++; await ok(route, {}); return true }
      return false
    })
    await page.goto(pageType === 'HOME' ? '/pages/home' : `/pages/micro?pageId=${ids.a}`)
    if (pageType === 'MICRO') {
      if (testInfo.project.name === 'mobile') await page.getByRole('button', { name: '组件', exact: true }).click()
      await page.getByRole('button', { name: '商品营销', exact: true }).click()
    }
    await page.locator('.home-component-library').getByRole('button', { name: '优惠券列表', exact: true }).click()
    await page.getByLabel('优惠券来源', { exact: true }).selectOption('MANUAL')
    await page.getByLabel('优惠券活动 ID', { exact: true }).fill(ids.b)
    await page.getByLabel('优惠券活动 ID', { exact: true }).press('Tab')
    await page.getByRole('button', { name: '检查发布差异与引用', exact: true }).click()
    await expect(page.locator('.release-report-issues')).toContainText('优惠券活动尚未公开。')
    expect(saved.config.schemaVersion).toBe(4)
    expect(saved.config.components[0].props.campaignIds).toEqual([ids.b])
    expect(reportReads).toBe(1); expect(publishWrites).toBe(0)
    await page.getByLabel('优惠券展示数量', { exact: true }).fill('2')
    await page.getByLabel('优惠券展示数量', { exact: true }).press('Tab')
    await expect(page.getByText(/报告已过期/)).toBeVisible()
    await screenshot(page, testInfo, `phase3-${pageType.toLowerCase()}-report`)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    verify()
  })
}
