/** Real Vue pages and Chromium interactions; HTTP data/failures are explicitly mocked. */
import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { asset, ids, imageBytes, installMockApi, ok, screenshot } from './fixtures'

test('mock HTTP: coupon browser-back route update protects dirty details', async ({ page }, testInfo) => {
  const verify = await installMockApi(page)
  await page.goto(`/coupons/${ids.a}`)
  await expect(page.getByLabel('活动名称', { exact: true })).toHaveValue('活动甲')
  // Seed adjacent detail history using the real application router. The action under test is browser Back.
  await page.evaluate(async id => {
    const { router } = await import('/src/router.ts')
    await router.push(`/coupons/${id}`)
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
